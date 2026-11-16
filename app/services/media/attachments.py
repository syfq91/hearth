"""Extract container attachments (eg. embedded fonts) with disk caching.

The same cache is used by the attachment HTTP endpoint and by the transcode
pipeline, which needs a ``fontsdir`` so FFmpeg's ``subtitles`` filter can render
ASS/SSA burn-in with the fonts shipped inside the container.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from pathlib import Path

from app.core.ids import new_id
from app.services.media.probe import AttachmentStream

logger = logging.getLogger("hearth.media.attachments")

# Extraction is a header read for Matroska and MP4, so this only guards against
# a wedged ffmpeg rather than a slow scan of a huge file.
EXTRACT_TIMEOUT_SECONDS = 120.0

_FONT_SUFFIX_BY_MIME = {
    "application/x-truetype-font": ".ttf",
    "application/x-font-ttf": ".ttf",
    "application/vnd.ms-opentype": ".otf",
    "application/x-font-otf": ".otf",
    "font/ttf": ".ttf",
    "font/otf": ".otf",
    "font/sfnt": ".ttf",
    "font/woff": ".woff",
    "font/woff2": ".woff2",
    "font/collection": ".ttc",
}
_SAFE_FONT_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class AttachmentExtractionError(Exception):
    """An attachment stream could not be extracted from its container."""


def attachment_cache_path(cache_dir: Path, file_id: str, index: int) -> Path:
    """Return the deterministic cache path for one attachment stream."""
    return cache_dir / f"{file_id}_{index}.bin"


async def extract_attachment(
    video_path: Path,
    attachment: AttachmentStream,
    cache_path: Path,
    *,
    ffmpeg_binary: str = "ffmpeg",
    timeout: float = EXTRACT_TIMEOUT_SECONDS,
) -> Path:
    """Extract one attachment stream into ``cache_path`` and return the path.

    Cached bytes are reused; the extraction is written to a unique temp file and
    atomically renamed so a concurrent reader never sees a partial font.
    """
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        try:
            # Re-extract when the source was replaced after the cache was made.
            if cache_path.stat().st_mtime >= video_path.stat().st_mtime:
                return cache_path
        except OSError:
            return cache_path

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cache_path.with_name(f"{cache_path.name}.tmp.{new_id()}")
    cmd = [
        ffmpeg_binary,
        "-y",
        f"-dump_attachment:{attachment.index}",
        str(temp_path),
        "-i",
        str(video_path),
        "-t",
        "0",
        "-f",
        "null",
        "-",
    ]
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0 or not temp_path.is_file() or temp_path.stat().st_size == 0:
            raise AttachmentExtractionError(
                f"ffmpeg exit {proc.returncode}: {stderr.decode('utf-8', errors='replace')[:500]}"
            )
        temp_path.replace(cache_path)
        return cache_path
    except TimeoutError as exc:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise AttachmentExtractionError("attachment extraction timed out") from exc
    except OSError as exc:
        raise AttachmentExtractionError(str(exc)) from exc
    except AttachmentExtractionError:
        raise
    finally:
        temp_path.unlink(missing_ok=True)


def font_filename(attachment: AttachmentStream) -> str:
    """Return a safe, extension-carrying filename for a font attachment.

    libass/fontconfig read font files by content, but a sensible extension keeps
    directory scans and debugging predictable.
    """
    raw = Path(attachment.filename or "").name
    if not raw or raw in (".", ".."):
        raw = f"attachment-{attachment.index}"
    if not Path(raw).suffix:
        raw += _FONT_SUFFIX_BY_MIME.get((attachment.mime_type or "").lower(), ".ttf")
    safe = _SAFE_FONT_NAME.sub("_", raw).strip("._-") or f"attachment-{attachment.index}"
    return f"{attachment.index}-{safe}"


async def prepare_font_directory(
    video_path: Path,
    attachments: list[AttachmentStream],
    fonts_dir: Path,
    *,
    cache_dir: Path,
    file_id: str,
    ffmpeg_binary: str = "ffmpeg",
) -> Path | None:
    """Extract every font attachment into ``fonts_dir`` for libass.

    Returns the directory when at least one font was extracted, otherwise
    ``None``. One broken attachment must not stop the others or the transcode.
    """
    fonts = [attachment for attachment in attachments if attachment.is_font]
    if not fonts:
        return None

    fonts_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0
    for attachment in fonts:
        try:
            cached = await extract_attachment(
                video_path,
                attachment,
                attachment_cache_path(cache_dir, file_id, attachment.index),
                ffmpeg_binary=ffmpeg_binary,
            )
            destination = fonts_dir / font_filename(attachment)
            if not destination.exists():
                await asyncio.to_thread(_link_or_copy, cached, destination)
            extracted += 1
        except AttachmentExtractionError as exc:
            logger.warning(
                "failed to extract font attachment %s from %s: %s",
                attachment.index,
                video_path,
                exc,
            )
    return fonts_dir if extracted else None


def _link_or_copy(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


__all__ = [
    "AttachmentExtractionError",
    "EXTRACT_TIMEOUT_SECONDS",
    "attachment_cache_path",
    "extract_attachment",
    "font_filename",
    "prepare_font_directory",
]
