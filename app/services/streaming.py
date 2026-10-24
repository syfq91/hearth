"""HTTP byte-range handling and async file streaming for direct play.

Range parsing is pure and unit tested. The reader moves blocking file I/O to a
thread so a slow disk or network mount cannot stall the event loop while other
requests and streams are being served.
"""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

_CONTENT_TYPES = {
    ".mkv": "video/x-matroska",
    ".mp4": "video/mp4",
    ".m4v": "video/x-m4v",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".ts": "video/mp2t",
    ".m2ts": "video/mp2t",
    ".flv": "video/x-flv",
    ".wmv": "video/x-ms-wmv",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
    ".ogv": "video/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".opus": "audio/ogg",
    ".ogg": "audio/ogg",
    ".wav": "audio/wav",
}


class RangeNotSatisfiable(Exception):
    """The requested byte range lies outside the representation."""

    def __init__(self, size: int) -> None:
        super().__init__(f"Range is not satisfiable for a {size}-byte file")
        self.size = size


@dataclass(frozen=True)
class ByteRange:
    """An inclusive byte range within a file."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    def content_range(self, size: int) -> str:
        return f"bytes {self.start}-{self.end}/{size}"


def parse_range_header(header: str | None, size: int) -> ByteRange | None:
    """Parse a single ``bytes`` range.

    Returns ``None`` when the whole representation should be served: no header,
    an unknown unit, or a syntactically invalid/unsupported header (multiple
    ranges). Raises :class:`RangeNotSatisfiable` for a syntactically valid range
    that starts beyond the end of the file, which callers answer with 416.
    """
    if header is None:
        return None
    value = header.strip()
    if not value:
        return None
    if not value.lower().startswith("bytes="):
        return None

    spec = value[len("bytes=") :].strip()
    if not spec or "," in spec:
        return None

    first, separator, last = spec.partition("-")
    if not separator:
        return None
    first = first.strip()
    last = last.strip()

    if size <= 0:
        raise RangeNotSatisfiable(size)

    if not first:
        if not last.isdigit():
            return None
        suffix = int(last)
        if suffix == 0:
            raise RangeNotSatisfiable(size)
        return ByteRange(max(0, size - suffix), size - 1)

    if not first.isdigit():
        return None
    start = int(first)
    if start >= size:
        raise RangeNotSatisfiable(size)

    if not last:
        return ByteRange(start, size - 1)
    if not last.isdigit():
        return None
    end = int(last)
    if end < start:
        return None
    return ByteRange(start, min(end, size - 1))


def content_type_for(path: str | Path) -> str:
    """Return a media content type from the file extension."""
    extension = Path(path).suffix.casefold()
    return _CONTENT_TYPES.get(extension, "application/octet-stream")


async def iter_file_chunks(
    path: Path,
    byte_range: ByteRange,
    *,
    chunk_size: int,
) -> AsyncIterator[bytes]:
    """Yield the requested range of a file in bounded chunks.

    Blocking reads and the seek run in a worker thread; the handle is always
    closed, including when the client disconnects mid-stream.
    """
    handle = await asyncio.to_thread(path.open, "rb")
    try:
        await asyncio.to_thread(handle.seek, byte_range.start)
        remaining = byte_range.length
        while remaining > 0:
            chunk = await asyncio.to_thread(handle.read, min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        await asyncio.to_thread(handle.close)
