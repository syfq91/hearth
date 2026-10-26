"""Download and cache artwork, then derive resized variants on demand.

The pipeline doubles as the matcher's optional artwork callback, so metadata
matching never imports the image layer directly. Originals are cached under
``images/originals/<item>/`` and variants under ``images/variants/<image>/``.

Security: remote artwork is only fetched over http(s) from an allow-listed host
(``image_allowed_hosts``), redirects are not followed, and downloads are capped
by ``image_max_download_bytes``. Local NFO artwork is read from disk directly
and never treated as a URL.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import blurhash
import httpx
from PIL import Image as PILImage
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ValidationError
from app.core.ids import new_id
from app.db.models import Image as ImageModel
from app.services.metadata.base import ProviderImage

logger = logging.getLogger("hearth.images")

# A variant format maps to (file extension, Pillow format name).
_VARIANT_FORMATS: dict[str, tuple[str, str]] = {
    "webp": ("webp", "WEBP"),
    "jpeg": ("jpg", "JPEG"),
    "jpg": ("jpg", "JPEG"),
    "png": ("png", "PNG"),
}
_CONTENT_TYPE_EXT: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
_PATH_EXT: dict[str, str] = {
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".png": "png",
    ".webp": "webp",
}
_MIN_VARIANT_WIDTH = 16
_MAX_VARIANT_WIDTH = 4096
_MAX_KIND_LENGTH = 16

_POOL: ProcessPoolExecutor | None = None
_POOL_DISABLED = False
_POOL_LOCK = threading.Lock()


# --------------------------------------------------------------------- helpers


def _get_pool(max_workers: int) -> ProcessPoolExecutor | None:
    """Return the shared Pillow process pool, or ``None`` if unavailable."""
    global _POOL, _POOL_DISABLED
    if _POOL_DISABLED:
        return None
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None and not _POOL_DISABLED:
                try:
                    _POOL = ProcessPoolExecutor(max_workers=max(1, max_workers))
                except (OSError, ValueError):  # pragma: no cover - platform dependent
                    _POOL_DISABLED = True
                    logger.warning("process pool unavailable; using threads for image work")
    return _POOL


async def _run_in_pool(func: Any, *args: Any, max_workers: int = 2) -> Any:
    """Run a CPU-bound Pillow helper in the process pool, falling back to threads."""
    pool = _get_pool(max_workers)
    if pool is None:
        return await asyncio.to_thread(func, *args)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(pool, func, *args)


def shutdown_image_pool() -> None:
    """Shut down the shared process pool (used by tests and app shutdown)."""
    global _POOL
    with _POOL_LOCK:
        if _POOL is not None:
            _POOL.shutdown(wait=False)
            _POOL = None


def _to_rows(image: PILImage.Image) -> list[list[tuple[int, int, int]]]:
    """Flatten a PIL image into the ``image[y][x]`` rows ``blurhash`` expects."""
    width, height = image.size
    flattened = getattr(image, "get_flattened_data", None)
    pixels = list(flattened()) if callable(flattened) else list(image.getdata())
    return [pixels[y * width : (y + 1) * width] for y in range(height)]


def _ext_from_path(path: str) -> str | None:
    return _PATH_EXT.get(Path(path).suffix.casefold())


def _sanitize_kind(kind: str) -> str:
    cleaned = kind.strip().casefold() or "poster"
    return cleaned[:_MAX_KIND_LENGTH]


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _inspect_image(path: str, components: int) -> tuple[int, int, str | None, str | None]:
    """Return ``(width, height, blurhash, pillow_format)`` for a stored original."""
    with PILImage.open(path) as image:
        width, height = image.size
        image_format = (image.format or "").lower() or None
        thumbnail = image.convert("RGB")
        thumbnail.thumbnail((32, 32))
        try:
            value = blurhash.encode(
                _to_rows(thumbnail),
                components_x=components,
                components_y=components,
            )
        except Exception:  # pragma: no cover - blurhash is best effort
            value = None
    return width, height, value, image_format


def _render_variant(source: str, target: str, width: int, image_format: str, quality: int) -> None:
    """Resize one image to ``width`` and save it in the requested format."""
    with PILImage.open(source) as image:
        output: PILImage.Image = image
        if image.width > width:
            ratio = width / image.width
            height = max(1, round(image.height * ratio))
            output = image.resize((width, height), PILImage.Resampling.LANCZOS)
        if image_format == "JPEG":
            output.convert("RGB").save(target, "JPEG", quality=quality, optimize=True)
        elif image_format == "WEBP":
            if output.mode not in ("RGB", "RGBA"):
                output = output.convert("RGBA" if "A" in output.getbands() else "RGB")
            output.save(target, "WEBP", quality=quality, method=4)
        else:
            output.save(target, "PNG", optimize=True)


# ----------------------------------------------------------------------- types


@dataclass(frozen=True)
class StoredImage:
    """The persisted original plus the metadata read from its pixels."""

    id: str
    kind: str
    local_path: str
    width: int
    height: int
    blurhash: str | None


# --------------------------------------------------------------------- pipeline


class ImagePipeline:
    """Cache originals and render variants for catalog artwork."""

    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self._client = client
        self._owns_client = client is None
        self._client_lock = asyncio.Lock()

    # Matcher artwork callback: ``(item_id, provider, images) -> stored count``.
    async def __call__(
        self,
        item_id: str,
        provider_name: str,
        images: Any,
    ) -> int:
        return await self.store_for_item(item_id, provider_name, images)

    @property
    def images_dir(self) -> Path:
        return self.settings.images_dir

    async def store_for_item(self, item_id: str, provider_name: str, images: Any) -> int:
        """Store provider artwork, skipping origins already cached for the item."""
        candidates = list(images or [])
        if not candidates:
            return 0

        known = await self._known_origins(item_id)
        stored = 0
        for provider_image in candidates:
            origin = provider_image.source_url or provider_image.file_path
            if not origin or origin in known:
                continue
            try:
                result = await self.store_image(item_id, provider_name, provider_image)
            except Exception:
                logger.warning(
                    "failed to store artwork",
                    extra={"item_id": item_id, "provider": provider_name},
                )
                continue
            if result is None:
                continue
            known.add(origin)
            stored += 1
        return stored

    async def store_image(
        self, item_id: str, provider_name: str, provider_image: ProviderImage
    ) -> StoredImage | None:
        """Download or copy one image and write its catalog row."""
        payload: tuple[bytes, str] | None
        if provider_image.data:
            ext = _CONTENT_TYPE_EXT.get(provider_image.mime_type or "image/jpeg", "jpg")
            payload = (provider_image.data, ext)
        elif provider_image.source_url:
            payload = await self._download(provider_image.source_url)
        elif provider_image.file_path:
            payload = await self._read_local(Path(provider_image.file_path))
        else:
            return None
        if payload is None:
            return None
        data, extension = payload

        image_id = new_id()
        destination = self._original_path(item_id, image_id, extension)
        await asyncio.to_thread(_write_bytes, destination, data)
        try:
            width, height, blurhash_value, _ = await _run_in_pool(
                _inspect_image,
                str(destination),
                self.settings.image_blurhash_components,
                max_workers=self.settings.image_process_workers,
            )
        except Exception:
            await asyncio.to_thread(destination.unlink, True)
            raise

        kind = _sanitize_kind(provider_image.kind)
        origin = provider_image.source_url or provider_image.file_path or f"embedded:{item_id}"
        async with self.sessionmaker() as session:
            if provider_image.is_primary:
                await session.execute(
                    update(ImageModel)
                    .where(ImageModel.item_id == item_id, ImageModel.kind == kind)
                    .values(is_primary=False)
                )
            session.add(
                ImageModel(
                    id=image_id,
                    item_id=item_id,
                    kind=kind,
                    provider=provider_name,
                    source_url=origin,
                    local_path=str(destination),
                    width=width,
                    height=height,
                    blurhash=blurhash_value,
                    is_primary=provider_image.is_primary,
                )
            )
            await session.commit()

        return StoredImage(
            id=image_id,
            kind=kind,
            local_path=str(destination),
            width=width,
            height=height,
            blurhash=blurhash_value,
        )

    # ------------------------------------------------------------- variants

    def variant_path(self, image_id: str, *, width: int, image_format: str) -> Path:
        """Return the cache path for a variant, validating the request."""
        extension, _ = self._validate_variant(width, image_format)
        return self.images_dir / "variants" / image_id / f"{width}w.{extension}"

    async def ensure_variant(
        self,
        image_id: str,
        local_path: str,
        *,
        width: int,
        image_format: str,
    ) -> Path:
        """Render a variant if missing and return its path."""
        _, pillow_format = self._validate_variant(width, image_format)
        target = self.variant_path(image_id, width=width, image_format=image_format)
        if target.is_file():
            return target

        source = Path(local_path)
        if not source.is_file():
            raise ValidationError("Artwork original is missing", code="image_missing")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            await _run_in_pool(
                _render_variant,
                str(source),
                str(temporary),
                width,
                pillow_format,
                self.settings.image_variant_quality,
                max_workers=self.settings.image_process_workers,
            )
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return target

    def _validate_variant(self, width: int, image_format: str) -> tuple[str, str]:
        entry = _VARIANT_FORMATS.get(image_format.casefold())
        if entry is None:
            raise ValidationError(
                f"Unsupported image format {image_format!r}",
                code="unsupported_image_format",
            )
        if not _MIN_VARIANT_WIDTH <= width <= _MAX_VARIANT_WIDTH:
            raise ValidationError(
                f"Image width must be between {_MIN_VARIANT_WIDTH} and {_MAX_VARIANT_WIDTH}",
                code="invalid_image_width",
            )
        return entry

    # -------------------------------------------------------------- internals

    async def _known_origins(self, item_id: str) -> set[str]:
        async with self.sessionmaker() as session:
            rows = await session.scalars(
                select(ImageModel.source_url).where(
                    ImageModel.item_id == item_id,
                    ImageModel.source_url.is_not(None),
                )
            )
            return {origin for origin in rows if origin}

    def _original_path(self, item_id: str, image_id: str, extension: str) -> Path:
        return self.images_dir / "originals" / item_id / f"{image_id}.{extension}"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            async with self._client_lock:
                if self._client is None:
                    self._client = httpx.AsyncClient(
                        timeout=self.settings.image_download_timeout_seconds,
                        follow_redirects=False,
                        headers={"User-Agent": "Hearth/0.1 (+https://github.com/hearth)"},
                    )
        return self._client

    async def _download(self, url: str) -> tuple[bytes, str] | None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        host = (parsed.hostname or "").casefold()
        allowed = {entry.casefold() for entry in self.settings.image_allowed_hosts}
        if allowed and host not in allowed:
            logger.warning("refusing artwork download", extra={"host": host})
            return None

        client = await self._get_client()
        maximum = self.settings.image_max_download_bytes
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                return None
            content_type = response.headers.get("content-type", "").split(";")[0]
            extension = _CONTENT_TYPE_EXT.get(content_type.strip().casefold())
            if extension is None:
                extension = _ext_from_path(parsed.path)
            if extension is None:
                return None
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > maximum:
                    logger.warning("artwork download exceeded size limit", extra={"url": url})
                    return None
                chunks.append(chunk)
        return b"".join(chunks), extension

    async def _read_local(self, path: Path) -> tuple[bytes, str] | None:
        try:
            if not path.is_file():
                return None
            if path.stat().st_size > self.settings.image_max_download_bytes:
                return None
        except OSError:
            return None
        extension = _ext_from_path(str(path))
        if extension is None:
            return None
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except OSError:
            return None
        return data, extension

    async def close(self) -> None:
        """Close the HTTP client this pipeline created, if any."""
        if not self._owns_client:
            return
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # pragma: no cover - close must never mask a match
                logger.warning("failed to close image client")
