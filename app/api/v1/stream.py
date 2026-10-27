"""Direct-play streaming with HTTP range support."""

from email.utils import formatdate
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import Headers

from app.core.deps import (
    SettingsDep,
    Viewer,
    ViewerDep,
    user_can_access_library,
)
from app.core.errors import NotFoundError
from app.db.models import Episode, LibraryPath, MediaFile, MediaItem, Track
from app.db.session import SessionDep
from app.services.libraries import path_is_within_roots
from app.services.streaming import (
    ByteRange,
    RangeNotSatisfiable,
    content_type_for,
    iter_file_chunks,
    parse_range_header,
)

router = APIRouter(tags=["stream"])


@router.get("/stream/{file_id}")
async def stream_file(
    file_id: str,
    request: Request,
    viewer: ViewerDep,
    settings: SettingsDep,
    session: SessionDep,
) -> Response:
    """Stream a registered media file, honoring a single byte range.

    Accepts a bearer token or a media token in ``?token=``. The path must be a
    registered ``media_file`` inside one of its library's roots, so a tampered
    database row cannot turn this endpoint into arbitrary file access.
    """
    return await _stream_response(
        file_id,
        request.method,
        request.headers,
        viewer,
        settings.stream_chunk_bytes,
        session,
    )


@router.get("/stream/audio/{track_id}")
async def stream_audio_track(
    track_id: str,
    request: Request,
    viewer: ViewerDep,
    settings: SettingsDep,
    session: SessionDep,
) -> Response:
    """Stream an audio track by its item ID, honoring a single byte range."""
    track = await session.get(Track, track_id)
    if track is None or not track.file_id:
        raise NotFoundError("Track audio file not found", code="track_not_found")
    return await _stream_response(
        track.file_id,
        request.method,
        request.headers,
        viewer,
        settings.stream_chunk_bytes,
        session,
    )


@router.head("/stream/audio/{track_id}", include_in_schema=False)
async def stream_audio_track_head(
    track_id: str,
    request: Request,
    viewer: ViewerDep,
    settings: SettingsDep,
    session: SessionDep,
) -> Response:
    """Return the same headers as a GET without opening the audio file."""
    track = await session.get(Track, track_id)
    if track is None or not track.file_id:
        raise NotFoundError("Track audio file not found", code="track_not_found")
    return await _stream_response(
        track.file_id,
        request.method,
        request.headers,
        viewer,
        settings.stream_chunk_bytes,
        session,
    )


@router.head("/stream/{file_id}", include_in_schema=False)
async def stream_file_head(
    file_id: str,
    request: Request,
    viewer: ViewerDep,
    settings: SettingsDep,
    session: SessionDep,
) -> Response:
    """Return the same headers as a GET without opening the file."""
    return await _stream_response(
        file_id,
        request.method,
        request.headers,
        viewer,
        settings.stream_chunk_bytes,
        session,
    )


async def stream_media_file(
    file_id: str,
    method: str,
    request_headers: Headers,
    viewer: Viewer,
    chunk_size: int,
    session: AsyncSession,
    *,
    download_filename: str | None = None,
) -> Response:
    media_file = await session.get(MediaFile, file_id)
    if media_file is None or media_file.missing_since is not None:
        raise NotFoundError("Media file not found", code="file_not_found")
    if viewer.item_id is not None and viewer.item_id != media_file.item_id:
        is_episode_file = await session.scalar(
            select(func.count(Episode.item_id)).where(
                Episode.item_id == viewer.item_id,
                Episode.file_id == media_file.id,
            )
        )
        is_track_file = await session.scalar(
            select(func.count(Track.item_id)).where(
                Track.item_id == viewer.item_id,
                Track.file_id == media_file.id,
            )
        )
        if not is_episode_file and not is_track_file:
            raise NotFoundError("Media file not found", code="file_not_found")

    item = await session.get(MediaItem, media_file.item_id)
    if item is None or not await user_can_access_library(session, viewer.user, item.library_id):
        raise NotFoundError("Media file not found", code="file_not_found")

    roots = list(
        await session.scalars(
            select(LibraryPath.path).where(LibraryPath.library_id == item.library_id)
        )
    )
    path = Path(media_file.path)
    if not path_is_within_roots(path, roots):
        raise NotFoundError("Media file not found", code="file_not_found")
    try:
        stat = path.stat()
    except OSError:
        raise NotFoundError("Media file not found", code="file_not_found") from None
    if not path.is_file():
        raise NotFoundError("Media file not found", code="file_not_found")

    size = stat.st_size
    etag = f'"{media_file.partial_hash or media_file.id}"'
    last_modified = formatdate(stat.st_mtime, usegmt=True)
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": last_modified,
        "Cache-Control": "private, max-age=0, must-revalidate",
        "X-Content-Type-Options": "nosniff",
    }
    if download_filename:
        safe_name = download_filename.replace('"', '\\"')
        headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    media_type = content_type_for(path)

    if _matches_etag(request_headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)

    if method == "HEAD":
        headers["Content-Length"] = str(size)
        return Response(status_code=200, headers=headers, media_type=media_type)

    range_header = request_headers.get("range")
    if_range = request_headers.get("if-range")
    if if_range is not None and if_range.strip() not in {etag, last_modified}:
        range_header = None

    try:
        byte_range = parse_range_header(range_header, size)
    except RangeNotSatisfiable:
        headers["Content-Range"] = f"bytes */{size}"
        return Response(status_code=416, headers=headers)

    status_code = 200
    if byte_range is None:
        byte_range = ByteRange(0, max(-1, size - 1))
    else:
        status_code = 206
        headers["Content-Range"] = byte_range.content_range(size)
    headers["Content-Length"] = str(byte_range.length)

    return StreamingResponse(
        iter_file_chunks(path, byte_range, chunk_size=chunk_size),
        status_code=status_code,
        media_type=media_type,
        headers=headers,
    )


_stream_response = stream_media_file


def _matches_etag(header: str | None, etag: str) -> bool:
    """Return whether an ``If-None-Match`` header matches the representation."""
    if not header:
        return False
    candidates = [value.strip() for value in header.split(",")]
    return "*" in candidates or etag in candidates
