"""Serve extracted media attachments (e.g. fonts) on demand with disk caching."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.core.deps import SettingsDep, ViewerDep, user_can_access_library
from app.core.errors import NotFoundError
from app.db.models import Episode, LibraryPath, MediaFile, MediaItem, Track
from app.db.session import SessionDep
from app.services.libraries import path_is_within_roots
from app.services.media.attachments import (
    AttachmentExtractionError,
    attachment_cache_path,
    extract_attachment,
)
from app.services.media.probe import ProbeResult

router = APIRouter(prefix="/media", tags=["media"])

_FONT_MIME_BY_EXT = {
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttc": "font/collection",
}


@router.get(
    "/{file_id}/attachments/{attachment_index}",
    response_class=FileResponse,
    responses={
        200: {
            "description": "Extracted media attachment file (font, image, etc.)",
            "content": {
                "font/ttf": {"schema": {"type": "string", "format": "binary"}},
                "font/otf": {"schema": {"type": "string", "format": "binary"}},
                "font/woff": {"schema": {"type": "string", "format": "binary"}},
                "font/woff2": {"schema": {"type": "string", "format": "binary"}},
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
async def get_attachment(
    file_id: str,
    attachment_index: int,
    viewer: ViewerDep,
    settings: SettingsDep,
    session: SessionDep,
) -> FileResponse:
    """Return an extracted container attachment (e.g. embedded font) by stream index.

    Authenticates via bearer token or short-lived media token in ``?token=``.
    Extracts the attachment stream on-the-fly via FFmpeg and caches it under
    ``HEARTH_TRANSCODE_DIR/fonts/``.
    """
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
    video_path = Path(media_file.path)
    if not path_is_within_roots(video_path, roots):
        raise NotFoundError("Media file not found", code="file_not_found")

    # Locate attachment metadata in stored streams
    probe_res: ProbeResult
    try:
        if isinstance(media_file.streams_json, dict) and "container" in media_file.streams_json:
            probe_res = ProbeResult.model_validate(media_file.streams_json)
        elif isinstance(media_file.streams_json, str):
            probe_res = ProbeResult.model_validate_json(media_file.streams_json)
        else:
            probe_res = ProbeResult(
                container=media_file.container or "mp4",
                duration_seconds=media_file.duration_seconds,
                size_bytes=media_file.size_bytes,
            )
    except Exception:
        probe_res = ProbeResult(
            container=media_file.container or "mp4",
            duration_seconds=media_file.duration_seconds,
            size_bytes=media_file.size_bytes,
        )

    attachment = next(
        (att for att in probe_res.all_attachments if att.index == attachment_index),
        None,
    )
    if attachment is None:
        raise NotFoundError("Attachment not found", code="attachment_not_found")

    media_type = attachment.mime_type
    if not media_type or media_type.lower() == "application/octet-stream":
        suffix = Path(attachment.filename or "").suffix.lower()
        media_type = _FONT_MIME_BY_EXT.get(suffix, "application/octet-stream")

    fonts_dir = Path(settings.transcode_dir) / "fonts"
    cache_path = attachment_cache_path(fonts_dir, media_file.id, attachment_index)

    try:
        await extract_attachment(
            video_path,
            attachment,
            cache_path,
            ffmpeg_binary=settings.ffmpeg_binary,
        )
    except AttachmentExtractionError as exc:
        raise NotFoundError(
            "Attachment could not be extracted",
            code="attachment_not_found",
        ) from exc

    return FileResponse(
        cache_path,
        media_type=media_type,
        filename=attachment.filename,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )
