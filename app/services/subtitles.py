"""Sidecar subtitle discovery, opaque ids and WebVTT conversion.

Subtitle conversion is pure so it can be tested without files. The endpoint
resolves an opaque id against the live sidecar list next to the video, which
means a stale id 404s instead of serving a renamed or replaced file.
"""

import asyncio
import base64
import binascii
import html
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.services.scanner.walker import find_sidecar_subtitles

logger = logging.getLogger("hearth.subtitles")

SUPPORTED_SUFFIXES = frozenset({".srt", ".ass", ".ssa", ".vtt"})

_SRT_TIME = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[,.](\d{1,3})")
_VTT_TIME = re.compile(r"(\d{2,}):(\d{2}):(\d{2})\.(\d{3})")
_ASS_TIME = re.compile(r"^\s*(\d+):(\d{1,2}):(\d{1,2})[.,](\d{1,3})\s*$")
_ASS_OVERRIDE = re.compile(r"\{[^}]*\}")
_ASS_EVENTS = re.compile(r"^\s*\[events\]\s*$", re.IGNORECASE)
_SECTION = re.compile(r"^\s*\[[^\]]+\]\s*$")
_LANGUAGE_TAG = re.compile(r"[A-Za-z]{2,8}")


@dataclass(frozen=True)
class SubtitleRef:
    """The file a subtitle belongs to and its sidecar basename."""

    file_id: str
    name: str


def encode_subtitle_id(file_id: str, name: str) -> str:
    """Encode an opaque, URL-safe subtitle id."""
    raw = json.dumps({"f": file_id, "n": name}, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).rstrip(b"=").decode("ascii")


def decode_subtitle_id(value: str) -> SubtitleRef | None:
    """Decode an opaque subtitle id, rejecting anything path-like."""
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeError, binascii.Error):
        return None
    if not isinstance(payload, dict):
        return None
    file_id = payload.get("f")
    name = payload.get("n")
    if not isinstance(file_id, str) or not file_id:
        return None
    if not isinstance(name, str) or not name or Path(name).name != name:
        return None
    if name.startswith(".") or "\\" in name:
        return None
    return SubtitleRef(file_id=file_id, name=name)


def discover_subtitles(video_path: Path | str) -> list[Path]:
    """Return supported sidecars next to a video, sorted by name."""
    return [
        Path(path)
        for path in find_sidecar_subtitles(video_path)
        if Path(path).suffix.casefold() in SUPPORTED_SUFFIXES
    ]


def subtitle_language(video_path: Path | str, subtitle_path: Path | str) -> str | None:
    """Return the language tag from ``Movie.en.srt``-style names."""
    video_stem = Path(video_path).stem
    subtitle_stem = Path(subtitle_path).stem
    if not subtitle_stem.startswith(f"{video_stem}."):
        return None
    tag = subtitle_stem[len(video_stem) + 1 :].split(".", 1)[0]
    return tag.casefold() if _LANGUAGE_TAG.fullmatch(tag) else None


def decode_subtitle_bytes(data: bytes) -> str:
    """Decode a sidecar, tolerating UTF-8 (with BOM) and Windows-1252."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def convert_to_webvtt(content: str, suffix: str, shift_ms: int = 0) -> str:
    """Convert SRT, ASS/SSA or VTT content to a WebVTT document.

    ``shift_ms`` moves every cue boundary by that many milliseconds (positive
    shifts cues later), which is how clients apply a per-track sync offset to
    native text tracks.
    """
    extension = suffix.casefold()
    if extension not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported subtitle format: {suffix}")

    text = _normalize_newlines(content).lstrip("\ufeff")
    if extension == ".ass" or extension == ".ssa":
        cues = _ass_cues(text)
        body = "\n\n".join(cues)
        return shift_webvtt(f"WEBVTT\n\n{body}\n" if body else "WEBVTT\n\n", shift_ms)

    if extension == ".vtt" and text.lstrip().startswith("WEBVTT"):
        return shift_webvtt(_normalize_timestamps(text), shift_ms)

    body = _normalize_timestamps(text).lstrip("\n")
    document = f"WEBVTT\n\n{body}" if body else "WEBVTT\n\n"
    return shift_webvtt(document, shift_ms)


def shift_webvtt(document: str, shift_ms: int) -> str:
    """Shift every cue boundary in a WebVTT document by ``shift_ms``.

    Only timing lines are rewritten, so header metadata (for example
    ``X-TIMESTAMP-MAP``) and cue text are left untouched. Clamping at zero can
    merge a cue into the start of the track, which is the expected result for a
    sync calibration of a few seconds.
    """
    if shift_ms == 0:
        return document

    def shift_line(line: str) -> str:
        if "-->" not in line:
            return line

        def replace(match: re.Match[str]) -> str:
            hours, minutes, seconds, millis = match.groups()
            total_ms = (
                (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000
                + int(millis)
                + shift_ms
            )
            return _format_vtt_time(max(0, total_ms))

        return _VTT_TIME.sub(replace, line)

    return "\n".join(shift_line(line) for line in document.split("\n"))


def _normalize_newlines(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _normalize_timestamps(content: str) -> str:
    """Rewrite SRT-style timestamps as WebVTT timestamps."""

    def replace(match: re.Match[str]) -> str:
        hours, minutes, seconds, fraction = match.groups()
        millis = fraction.ljust(3, "0")[:3]
        return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}.{millis}"

    return _SRT_TIME.sub(replace, content)


def _ass_cues(content: str) -> list[str]:
    fields: list[str] = []
    in_events = False
    cues: list[tuple[int, str]] = []

    for line in content.split("\n"):
        stripped = line.strip()
        if _SECTION.match(stripped):
            in_events = bool(_ASS_EVENTS.match(stripped))
            continue
        if not in_events:
            continue
        if stripped.casefold().startswith("format:"):
            fields = [field.strip().casefold() for field in stripped.split(":", 1)[1].split(",")]
            continue
        if not stripped.casefold().startswith("dialogue:"):
            continue
        if not fields:
            fields = [
                "layer",
                "start",
                "end",
                "style",
                "name",
                "marginl",
                "marginr",
                "marginv",
                "effect",
                "text",
            ]
        values = stripped.split(":", 1)[1].lstrip().split(",", len(fields) - 1)
        if len(values) != len(fields):
            continue
        row = dict(zip(fields, values, strict=False))
        start = _ass_millis(row.get("start", ""))
        end = _ass_millis(row.get("end", ""))
        text = _clean_ass_text(row.get("text", ""))
        if start is None or end is None or end <= start or not text:
            continue
        cue = f"{_format_vtt_time(start)} --> {_format_vtt_time(end)}\n{text}"
        cues.append((start, cue))

    cues.sort(key=lambda item: item[0])
    return [cue for _, cue in cues]


def _ass_millis(value: str) -> int | None:
    match = _ASS_TIME.match(value)
    if match is None:
        return None
    hours, minutes, seconds, fraction = match.groups()
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1000
        + int(fraction.ljust(3, "0")[:3])
    )


def _clean_ass_text(text: str) -> str:
    text = _ASS_OVERRIDE.sub("", text)
    text = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    return html.escape(text.strip(), quote=False)


def _format_vtt_time(total_ms: int) -> str:
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


async def extract_embedded_subtitle(
    video_path: Path | str,
    subtitle_stream_index: int,
    fmt: str = "webvtt",
    output_path: Path | None = None,
) -> str:
    """Extract an embedded subtitle track to WebVTT or ASS using FFmpeg."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        str(video_path),
        "-map",
        f"0:s:{subtitle_stream_index}",
        "-f",
        fmt,
        str(output_path) if output_path else "-",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE if not output_path else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        if proc.returncode != 0:
            logger.warning(
                "ffmpeg subtitle extraction failed (exit %d): %s",
                proc.returncode,
                stderr.decode("utf-8", errors="replace"),
            )
            return "WEBVTT\n\n" if fmt == "webvtt" else ""
        if output_path and output_path.exists():
            return await asyncio.to_thread(output_path.read_text, "utf-8", "replace")
        return (
            stdout.decode("utf-8", errors="replace")
            if stdout
            else ("WEBVTT\n\n" if fmt == "webvtt" else "")
        )
    except (OSError, TimeoutError) as exc:
        logger.warning("ffmpeg subtitle extraction error: %s", exc)
        return "WEBVTT\n\n" if fmt == "webvtt" else ""


_COMMON_LANGUAGE_ALIASES: dict[str, list[str]] = {
    "en": ["eng", "english"],
    "ja": ["jpn", "japanese"],
    "es": ["spa", "spanish", "espanol", "español"],
    "fr": ["fra", "fre", "french", "francais", "français"],
    "de": ["deu", "ger", "german", "deutsch"],
    "it": ["ita", "italian", "italiano"],
    "pt": ["por", "portuguese", "português"],
    "zh": ["zho", "chi", "chinese"],
    "ko": ["kor", "korean"],
    "ru": ["rus", "russian"],
    "ar": ["ara", "arabic"],
    "hi": ["hin", "hindi"],
    "nl": ["nld", "dut", "dutch"],
    "pl": ["pol", "polish"],
    "sv": ["swe", "swedish"],
    "tr": ["tur", "turkish"],
    "id": ["ind", "indonesian"],
    "vi": ["vie", "vietnamese"],
    "th": ["tha", "thai"],
}


def _has_word(text: str, word: str) -> bool:
    if not text or not word:
        return False
    return bool(re.search(rf"(^|[^a-z0-9]){re.escape(word)}([^a-z0-9]|$)", text, re.IGNORECASE))


def matches_language(
    track_lang: str | None,
    track_title: str | None = None,
    preferred: str | None = None,
) -> bool:
    """Return whether a stream language/title matches the preferred language tag."""
    if not preferred:
        return False
    pref = preferred.strip().casefold()
    if not pref:
        return False
    lang = (track_lang or "").strip().casefold()
    title = (track_title or "").strip().casefold()
    if lang == pref:
        return True
    if len(pref) == 2 and lang.startswith(pref):
        return True
    if len(lang) == 2 and pref.startswith(lang):
        return True
    for code, aliases in _COMMON_LANGUAGE_ALIASES.items():
        group = [code, *aliases]
        if any(pref == a for a in group) and any(lang == a or _has_word(title, a) for a in group):
            return True
    return _has_word(title, pref) if title else False


__all__ = [
    "SUPPORTED_SUFFIXES",
    "SubtitleRef",
    "convert_to_webvtt",
    "decode_subtitle_bytes",
    "decode_subtitle_id",
    "discover_subtitles",
    "encode_subtitle_id",
    "extract_embedded_subtitle",
    "matches_language",
    "shift_webvtt",
    "subtitle_language",
]
