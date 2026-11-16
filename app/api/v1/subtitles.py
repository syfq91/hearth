"""Serve external sidecar subtitles converted to WebVTT."""

import asyncio
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.deps import SettingsDep, Viewer, ViewerDep, user_can_access_library
from app.core.errors import AppError, NotFoundError
from app.db.models import Episode, LibraryPath, MediaFile, MediaItem
from app.db.session import SessionDep
from app.services.libraries import path_is_within_roots
from app.services.media.probe import ProbeResult
from app.services.subtitles import (
    EmbeddedSubtitleTrack,
    convert_to_webvtt,
    decode_subtitle_bytes,
    decode_subtitle_id,
    discover_subtitles,
    embedded_subtitle_cache_dir,
    ensure_embedded_subtitles,
    extract_embedded_subtitle,
    read_embedded_subtitle,
    shift_webvtt,
)
from app.services.transcode.decision import BITMAP_SUBTITLE_CODECS

_SUBTITLE_HEADERS = {
    "Cache-Control": "private, max-age=0, must-revalidate",
    "X-Content-Type-Options": "nosniff",
}

router = APIRouter(tags=["subtitles"])

_NOT_FOUND = "Subtitle not found"


async def _authorized_media_file(
    session: AsyncSession, viewer: Viewer, file_id: str
) -> tuple[MediaFile, list[str]]:
    """Load a registered media file the viewer may see, plus its library roots.

    Mirrors the attachment route: a media token scoped to an item is accepted
    when the file belongs to that item (or to an episode of it). Every path is
    re-validated against the library roots so a stale row cannot escape.
    """
    media_file = await session.get(MediaFile, file_id)
    if media_file is None or media_file.missing_since is not None:
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found")
    if viewer.item_id is not None and viewer.item_id != media_file.item_id:
        is_episode_file = await session.scalar(
            select(func.count(Episode.item_id)).where(
                Episode.item_id == viewer.item_id,
                Episode.file_id == media_file.id,
            )
        )
        if not is_episode_file:
            raise NotFoundError(_NOT_FOUND, code="subtitle_not_found")

    item = await session.get(MediaItem, media_file.item_id)
    if item is None or not await user_can_access_library(session, viewer.user, item.library_id):
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found")

    roots = list(
        await session.scalars(
            select(LibraryPath.path).where(LibraryPath.library_id == item.library_id)
        )
    )
    if not path_is_within_roots(Path(media_file.path), roots):
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found")
    return media_file, roots


def _probe_result(media_file: MediaFile) -> ProbeResult:
    """Return the stored probe streams, falling back to file metadata."""
    try:
        if isinstance(media_file.streams_json, dict) and "container" in media_file.streams_json:
            return ProbeResult.model_validate(media_file.streams_json)
        if isinstance(media_file.streams_json, str):
            return ProbeResult.model_validate_json(media_file.streams_json)
    except Exception:
        pass
    return ProbeResult(
        container=media_file.container or "mp4",
        duration_seconds=media_file.duration_seconds,
        size_bytes=media_file.size_bytes,
    )


def _text_subtitle_tracks(probe: ProbeResult) -> list[EmbeddedSubtitleTrack]:
    """Describe the non-bitmap subtitle streams the server can cache as text."""
    tracks: list[EmbeddedSubtitleTrack] = []
    ordinal = 0
    for stream in probe.streams:
        if stream.codec_type != "subtitle":
            continue
        codec = (stream.codec_name or "").lower()
        if codec not in BITMAP_SUBTITLE_CODECS:
            # ASS/SSA can also be delivered as WebVTT to clients without a
            # client-side renderer, so cache both representations in one pass.
            formats = ("ass", "vtt") if codec in {"ass", "ssa"} else ("vtt",)
            tracks.append(EmbeddedSubtitleTrack(ordinal=ordinal, formats=formats))
        ordinal += 1
    return tracks


async def _embedded_subtitle_response(
    media_file: MediaFile,
    stream_index: int,
    fmt: Literal["ass", "vtt"],
    settings: Settings,
    shift_ms: int = 0,
) -> Response:
    """Extract one embedded subtitle stream as WebVTT or ASS.

    ``stream_index`` is the ordinal among subtitle streams, matching the
    decision plan's ``subtitle_stream_index``. Every text track is extracted
    into a revision-keyed cache in a single demux pass, so switching tracks and
    replaying a title does not read the whole video again.
    """
    probe = _probe_result(media_file)
    subtitle_count = sum(1 for stream in probe.streams if stream.codec_type == "subtitle")
    if stream_index < 0 or stream_index >= subtitle_count:
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found")

    video_path = Path(media_file.path)
    cache_dir = embedded_subtitle_cache_dir(
        Path(settings.transcode_dir) / "subtitles",
        media_file.id,
        media_file.mtime,
        media_file.size_bytes,
    )
    await ensure_embedded_subtitles(
        video_path,
        _text_subtitle_tracks(probe),
        cache_dir,
        ffmpeg_binary=settings.ffmpeg_binary,
    )
    content = await read_embedded_subtitle(cache_dir, stream_index, fmt)
    if content is None:
        # Cached extraction failed (unsupported stream, missing file, ...).
        # Preserve the previous per-request behaviour and return an empty track.
        content = await extract_embedded_subtitle(video_path, stream_index, fmt=fmt)

    if fmt == "ass":
        return Response(content=content, media_type="text/x-ssa", headers=_SUBTITLE_HEADERS)
    return Response(
        content=shift_webvtt(content, shift_ms),
        media_type="text/vtt",
        headers=_SUBTITLE_HEADERS,
    )


@router.get(
    "/media/{file_id}/subtitles/{stream_index}.vtt",
    response_class=Response,
    responses={
        200: {
            "description": "Embedded subtitle extracted as WebVTT",
            "content": {"text/vtt": {"schema": {"type": "string"}}},
        }
    },
)
async def get_embedded_subtitle_vtt(
    file_id: str,
    stream_index: int,
    viewer: ViewerDep,
    settings: SettingsDep,
    session: SessionDep,
    shift_ms: int = Query(
        default=0,
        ge=-60_000,
        le=60_000,
        description="Shift cue timestamps by this many milliseconds; positive is later.",
    ),
) -> Response:
    """Serve an embedded (in-container) subtitle stream as WebVTT.

    Lets direct-play clients render text subtitles without a transcode session.
    """
    media_file, _roots = await _authorized_media_file(session, viewer, file_id)
    return await _embedded_subtitle_response(media_file, stream_index, "vtt", settings, shift_ms)


@router.get(
    "/media/{file_id}/subtitles/{stream_index}.ass",
    response_class=Response,
    responses={
        200: {
            "description": "Embedded subtitle extracted as ASS/SSA",
            "content": {"text/x-ssa": {"schema": {"type": "string"}}},
        }
    },
)
async def get_embedded_subtitle_ass(
    file_id: str,
    stream_index: int,
    viewer: ViewerDep,
    settings: SettingsDep,
    session: SessionDep,
) -> Response:
    """Serve an embedded subtitle stream as ASS for client-side rendering."""
    media_file, _roots = await _authorized_media_file(session, viewer, file_id)
    return await _embedded_subtitle_response(media_file, stream_index, "ass", settings)


@router.get(
    "/subtitles/{subtitle_id}.vtt",
    response_class=Response,
    responses={
        200: {
            "description": "WebVTT subtitle file",
            "content": {"text/vtt": {"schema": {"type": "string"}}},
        }
    },
)
async def get_subtitle(
    subtitle_id: str,
    viewer: ViewerDep,
    settings: SettingsDep,
    session: SessionDep,
    raw: bool = False,
    shift_ms: int = Query(
        default=0,
        ge=-60_000,
        le=60_000,
        description="Shift cue timestamps by this many milliseconds; positive is later.",
    ),
) -> Response:
    """Return a sidecar subtitle as WebVTT.

    The id is opaque and only names a basename; the file itself is resolved
    against the live sidecar list next to the registered video, so a stale or
    forged id cannot escape the library.
    """
    ref = decode_subtitle_id(subtitle_id)
    if ref is None:
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found")

    media_file, roots = await _authorized_media_file(session, viewer, ref.file_id)
    video = Path(media_file.path)

    # Re-run discovery on every request: deleting or replacing a sidecar must
    # invalidate previously handed-out ids.
    sidecar = next((path for path in discover_subtitles(video) if path.name == ref.name), None)
    if sidecar is None or not path_is_within_roots(sidecar, roots):
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found")

    try:
        stat = await asyncio.to_thread(sidecar.stat)
    except OSError:
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found") from None
    if stat.st_size > settings.subtitle_max_bytes:
        raise AppError(
            "Subtitle file is too large",
            code="subtitle_too_large",
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
        )

    try:
        data = await asyncio.to_thread(sidecar.read_bytes)
    except OSError:
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found") from None

    decoded = decode_subtitle_bytes(data)
    if raw:
        media_type = "text/x-ssa" if sidecar.suffix.casefold() in (".ass", ".ssa") else "text/plain"
        return Response(
            content=decoded,
            media_type=media_type,
            headers={
                "Cache-Control": "private, max-age=0, must-revalidate",
                "X-Content-Type-Options": "nosniff",
            },
        )

    try:
        document = convert_to_webvtt(decoded, sidecar.suffix, shift_ms)
    except ValueError:
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found") from None

    return Response(
        content=document,
        media_type="text/vtt",
        headers={
            "Cache-Control": "private, max-age=0, must-revalidate",
            "X-Content-Type-Options": "nosniff",
        },
    )


__all__ = ["router"]
