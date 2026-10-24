"""Serve external sidecar subtitles converted to WebVTT."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, status
from fastapi.responses import Response
from sqlalchemy import select

from app.core.deps import SettingsDep, ViewerDep, user_can_access_library
from app.core.errors import AppError, NotFoundError
from app.db.models import LibraryPath, MediaFile, MediaItem
from app.db.session import SessionDep
from app.services.libraries import path_is_within_roots
from app.services.subtitles import (
    convert_to_webvtt,
    decode_subtitle_bytes,
    decode_subtitle_id,
    discover_subtitles,
)

router = APIRouter(tags=["subtitles"])

_NOT_FOUND = "Subtitle not found"


@router.get("/subtitles/{subtitle_id}.vtt")
async def get_subtitle(
    subtitle_id: str,
    viewer: ViewerDep,
    settings: SettingsDep,
    session: SessionDep,
) -> Response:
    """Return a sidecar subtitle as WebVTT.

    The id is opaque and only names a basename; the file itself is resolved
    against the live sidecar list next to the registered video, so a stale or
    forged id cannot escape the library.
    """
    ref = decode_subtitle_id(subtitle_id)
    if ref is None:
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found")

    media_file = await session.get(MediaFile, ref.file_id)
    if media_file is None or media_file.missing_since is not None:
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found")
    if viewer.item_id is not None and viewer.item_id != media_file.item_id:
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found")

    item = await session.get(MediaItem, media_file.item_id)
    if item is None or not await user_can_access_library(session, viewer.user, item.library_id):
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found")

    roots = list(
        await session.scalars(
            select(LibraryPath.path).where(LibraryPath.library_id == item.library_id)
        )
    )
    video = Path(media_file.path)
    if not path_is_within_roots(video, roots):
        raise NotFoundError(_NOT_FOUND, code="subtitle_not_found")

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

    try:
        document = convert_to_webvtt(decode_subtitle_bytes(data), sidecar.suffix)
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
