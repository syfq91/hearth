"""Typed ffprobe wrapper.

ffprobe is always invoked with an argument list and a timeout. The parsed
result is JSON-serializable and is stored verbatim on ``MediaFile.streams_json``
so later playback decisions do not need to re-probe unchanged files.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_FFPROBE_TIMEOUT_SECONDS = 30.0
_HDR_TRANSFERS = {"smpte2084": "HDR10", "arib-std-b67": "HLG"}
_DOVI_TAGS = {"dvh1", "dvhe", "dav1"}
_DOVI_SIDE_DATA = {"DOVI configuration record", "Dolby Vision metadata"}

SUPPORTED_FONT_MIME_TYPES = frozenset(
    {
        "application/x-truetype-font",
        "application/vnd.ms-opentype",
        "application/x-font-ttf",
        "application/x-font-otf",
        "font/ttf",
        "font/otf",
        "font/woff",
        "font/woff2",
        "font/sfnt",
    }
)
FONT_EXTENSIONS = frozenset({".ttf", ".otf", ".woff", ".woff2", ".ttc"})


class AttachmentStream(BaseModel):
    """One embedded container attachment (e.g. font, cover art)."""

    index: int
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None

    @property
    def is_font(self) -> bool:
        """Whether this attachment appears to be a renderable font."""
        if self.mime_type and self.mime_type.lower() in SUPPORTED_FONT_MIME_TYPES:
            return True
        if self.filename:
            suffix = Path(self.filename).suffix.lower()
            if suffix in FONT_EXTENSIONS:
                return True
        return False


class ProbeError(Exception):
    """A media file could not be probed.

    Scanner callers record this per file and continue; one corrupt file must
    never fail an entire library scan.
    """

    def __init__(self, path: str, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


class ProbeStream(BaseModel):
    """One audio, video, subtitle or data stream."""

    index: int
    codec_type: str
    codec_name: str | None = None
    codec_long_name: str | None = None
    profile: str | None = None
    level: int | None = None
    width: int | None = None
    height: int | None = None
    pix_fmt: str | None = None
    bit_rate: int | None = None
    channels: int | None = None
    channel_layout: str | None = None
    sample_rate: int | None = None
    language: str | None = None
    title: str | None = None
    is_default: bool = False
    is_forced: bool = False
    is_hdr: bool = False
    hdr_format: str | None = None
    color_transfer: str | None = None
    codec_tag_string: str | None = None
    filename: str | None = None
    mime_type: str | None = None


class ProbeChapter(BaseModel):
    """One chapter marker."""

    index: int
    start_seconds: float
    end_seconds: float
    title: str | None = None


class ProbeResult(BaseModel):
    """The subset of ffprobe output Hearth needs."""

    container: str
    format_long_name: str | None = None
    duration_seconds: float | None = None
    bit_rate: int | None = None
    size_bytes: int | None = None
    streams: list[ProbeStream] = Field(default_factory=list)
    chapters: list[ProbeChapter] = Field(default_factory=list)
    attachments: list[AttachmentStream] = Field(default_factory=list)

    @property
    def video_streams(self) -> list[ProbeStream]:
        """Video streams in file order."""
        return [stream for stream in self.streams if stream.codec_type == "video"]

    @property
    def audio_streams(self) -> list[ProbeStream]:
        """Audio streams in file order."""
        return [stream for stream in self.streams if stream.codec_type == "audio"]

    @property
    def subtitle_streams(self) -> list[ProbeStream]:
        """Subtitle streams in file order."""
        return [stream for stream in self.streams if stream.codec_type == "subtitle"]

    @property
    def primary_video(self) -> ProbeStream | None:
        """The first video stream, which is the one playback defaults to."""
        videos = self.video_streams
        return videos[0] if videos else None

    @property
    def all_attachments(self) -> list[AttachmentStream]:
        """All container attachments, with fallback to stream inspection."""
        if self.attachments:
            return self.attachments
        fallback: list[AttachmentStream] = []
        for s in self.streams:
            if s.codec_type == "attachment":
                fallback.append(
                    AttachmentStream(
                        index=s.index,
                        filename=s.filename,
                        mime_type=s.mime_type,
                    )
                )
        return fallback

    @property
    def font_attachments(self) -> list[AttachmentStream]:
        """Font attachments suitable for ASS/SSA subtitle rendering."""
        return [att for att in self.all_attachments if att.is_font]


def _as_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _hdr_format(tags: dict[str, Any], side_data: list[Any]) -> str | None:
    """Classify HDR from codec tags and side data without guessing."""
    if str(tags.get("codec_tag_string", "")).lower() in _DOVI_TAGS:
        return "Dolby Vision"
    side_types = {
        str(entry.get("side_data_type"))
        for entry in side_data
        if isinstance(entry, dict) and entry.get("side_data_type")
    }
    if side_types & _DOVI_SIDE_DATA:
        return "Dolby Vision"
    return _HDR_TRANSFERS.get(str(tags.get("color_transfer") or ""))


def _parse_stream(stream: dict[str, Any]) -> ProbeStream:
    disposition = stream.get("disposition") or {}
    tags = stream.get("tags") or {}
    codec_type = str(stream.get("codec_type") or "unknown")
    hdr = _hdr_format(stream, stream.get("side_data_list") or []) if codec_type == "video" else None
    return ProbeStream(
        index=_as_int(stream.get("index")) or 0,
        codec_type=codec_type,
        codec_name=stream.get("codec_name"),
        codec_long_name=stream.get("codec_long_name"),
        profile=stream.get("profile"),
        level=_as_int(stream.get("level")),
        width=_as_int(stream.get("width")),
        height=_as_int(stream.get("height")),
        pix_fmt=stream.get("pix_fmt"),
        bit_rate=_as_int(stream.get("bit_rate")),
        channels=_as_int(stream.get("channels")),
        channel_layout=stream.get("channel_layout"),
        sample_rate=_as_int(stream.get("sample_rate")),
        language=tags.get("language"),
        title=tags.get("title"),
        is_default=bool(_as_int(disposition.get("default")) or 0),
        is_forced=bool(_as_int(disposition.get("forced")) or 0),
        is_hdr=hdr is not None,
        hdr_format=hdr,
        color_transfer=stream.get("color_transfer"),
        codec_tag_string=stream.get("codec_tag_string"),
        filename=tags.get("filename"),
        mime_type=tags.get("mimetype"),
    )


def parse_probe_payload(payload: dict[str, Any]) -> ProbeResult:
    """Convert decoded ffprobe JSON into a typed result."""
    format_info = payload.get("format") or {}
    streams = payload.get("streams") or []
    chapters = payload.get("chapters") or []
    if not isinstance(streams, list) or not isinstance(chapters, list):
        raise ValueError("ffprobe payload has malformed streams or chapters")

    parsed_streams = [_parse_stream(stream) for stream in streams if isinstance(stream, dict)]

    raw_attachments = payload.get("attachments")
    if isinstance(raw_attachments, list) and raw_attachments:
        attachment_objects = [
            AttachmentStream.model_validate(att)
            for att in raw_attachments
            if isinstance(att, dict)
        ]
    else:
        attachment_objects = [
            AttachmentStream(
                index=s.index,
                filename=s.filename,
                mime_type=s.mime_type,
            )
            for s in parsed_streams
            if s.codec_type == "attachment"
        ]

    return ProbeResult(
        container=str(format_info.get("format_name") or "unknown"),
        format_long_name=format_info.get("format_long_name"),
        duration_seconds=_as_float(format_info.get("duration")),
        bit_rate=_as_int(format_info.get("bit_rate")),
        size_bytes=_as_int(format_info.get("size")),
        streams=parsed_streams,
        chapters=[
            ProbeChapter(
                index=index,
                start_seconds=_as_float(chapter.get("start_time")) or 0.0,
                end_seconds=_as_float(chapter.get("end_time")) or 0.0,
                title=(chapter.get("tags") or {}).get("title"),
            )
            for index, chapter in enumerate(chapters)
            if isinstance(chapter, dict)
        ],
        attachments=attachment_objects,
    )


async def probe_media(
    path: Path | str,
    *,
    ffprobe_binary: str = "ffprobe",
    timeout: float = _FFPROBE_TIMEOUT_SECONDS,
) -> ProbeResult:
    """Run ffprobe on one file and return a typed result.

    Raises :class:`ProbeError` for missing binaries, timeouts, non-zero exits
    and malformed output. No shell is involved.
    """
    target = Path(path)
    command = [
        ffprobe_binary,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        str(target),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except (FileNotFoundError, PermissionError, NotADirectoryError):
        raise ProbeError(str(target), f"{ffprobe_binary} is not installed") from None

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ProbeError(str(target), f"ffprobe timed out after {timeout:g}s") from None

    if process.returncode:
        message = stderr.decode(errors="replace").strip().splitlines()
        raise ProbeError(
            str(target),
            message[0] if message else f"ffprobe exited with status {process.returncode}",
        )

    try:
        payload = json.loads(stdout.decode("utf-8", errors="replace"))
        if not isinstance(payload, dict):
            raise ValueError("ffprobe output is not a JSON object")
        return parse_probe_payload(payload)
    except (ValueError, TypeError) as exc:
        raise ProbeError(str(target), f"invalid ffprobe output: {exc}") from None
