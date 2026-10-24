"""Serve cached artwork originals as on-demand variants."""

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse

from app.core.deps import (
    SessionmakerDep,
    SettingsDep,
    ViewerDep,
    user_can_access_library,
)
from app.core.errors import NotFoundError
from app.db.models import Image as ImageModel
from app.db.models import MediaItem
from app.db.session import SessionDep
from app.services.images.pipeline import ImagePipeline

router = APIRouter(prefix="/images", tags=["images"])

_MEDIA_TYPES = {
    "webp": "image/webp",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


@router.get("/{image_id}")
async def get_image(
    image_id: str,
    viewer: ViewerDep,
    settings: SettingsDep,
    sessionmaker: SessionmakerDep,
    session: SessionDep,
    width: Annotated[int, Query(ge=16, le=4096)] = 400,
    image_format: Annotated[Literal["webp", "jpeg", "png"], Query(alias="format")] = "webp",
) -> FileResponse:
    """Return a resized artwork variant, authenticating by header or media token."""
    image = await session.get(ImageModel, image_id)
    if image is None:
        raise NotFoundError("Artwork not found", code="image_not_found")
    if viewer.item_id is not None and viewer.item_id != image.item_id:
        raise NotFoundError("Artwork not found", code="image_not_found")

    item = await session.get(MediaItem, image.item_id)
    if item is None or not await user_can_access_library(session, viewer.user, item.library_id):
        raise NotFoundError("Artwork not found", code="image_not_found")

    original = Path(image.local_path)
    if not original.is_file():
        raise NotFoundError("Artwork not found", code="image_not_found")

    pipeline = ImagePipeline(settings, sessionmaker)
    variant = await pipeline.ensure_variant(
        image.id, image.local_path, width=width, image_format=image_format
    )
    return FileResponse(
        variant,
        media_type=_MEDIA_TYPES[image_format],
        headers={
            "Cache-Control": "private, max-age=86400, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )
