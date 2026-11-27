"""Sidecar subtitle discovery, opaque ids and WebVTT conversion.

Subtitle conversion is pure so it can be tested without files. The endpoint
resolves an opaque id against the live sidecar list next to the video, which
means a stale id 404s instead of serving a renamed or replaced file.
"""

import asyncio
import base64
import binascii
import contextlib
import html
import json
import logging
import re
import shutil
import weakref
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.ids import new_id
from app.core.locks import LoopAwareLock
from app.services.scanner.walker import find_sidecar_subtitles

if TYPE_CHECKING:
    from app.services.media.probe import ProbeStream

_BITMAP_SUBTITLE_CODECS = frozenset(
    {"hdmv_pgs_subtitle", "dvd_subtitle", "dvdsub", "vobsub", "pgs", "pgssub", "xsub"}
)

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


# Reading every cluster of a large movie is I/O bound; an aggressive timeout
# silently produced empty subtitle tracks on slow storage before caching existed.
EMBEDDED_SUBTITLE_TIMEOUT_SECONDS = 300.0


async def extract_embedded_subtitle(
    video_path: Path | str,
    subtitle_stream_index: int,
    fmt: str = "webvtt",
    output_path: Path | None = None,
) -> str:
    """Extract an embedded subtitle track to WebVTT or ASS using FFmpeg."""
    muxer = "webvtt" if fmt in {"vtt", "webvtt"} else fmt
    empty = "WEBVTT\n\n" if muxer == "webvtt" else ""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        str(video_path),
        "-copyts",
        "-map",
        f"0:s:{subtitle_stream_index}",
        "-f",
        muxer,
        str(output_path) if output_path else "-",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE if not output_path else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=EMBEDDED_SUBTITLE_TIMEOUT_SECONDS
        )
        if proc.returncode != 0:
            logger.warning(
                "ffmpeg subtitle extraction failed (exit %d): %s",
                proc.returncode,
                stderr.decode("utf-8", errors="replace"),
            )
            return empty
        if output_path and output_path.exists():
            return await asyncio.to_thread(output_path.read_text, "utf-8", "replace")
        return stdout.decode("utf-8", errors="replace") if stdout else empty
    except (OSError, TimeoutError) as exc:
        if isinstance(exc, TimeoutError):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
        logger.warning("ffmpeg subtitle extraction error: %s", exc)
        return empty


@dataclass(frozen=True)
class EmbeddedSubtitleTrack:
    """A non-bitmap subtitle stream and the formats the server can cache.

    ``ordinal`` is the subtitle-only stream index used by FFmpeg's ``0:s:<n>``
    map, which is what the embedded-subtitle endpoint receives.
    """

    ordinal: int
    formats: tuple[str, ...]


def text_subtitle_tracks(streams: Sequence["ProbeStream"]) -> list[EmbeddedSubtitleTrack]:
    """Describe the non-bitmap subtitle streams the server can cache as text."""
    tracks: list[EmbeddedSubtitleTrack] = []
    ordinal = 0
    for stream in streams:
        if stream.codec_type != "subtitle":
            continue
        codec = (stream.codec_name or "").lower()
        if codec not in _BITMAP_SUBTITLE_CODECS:
            # ASS/SSA can also be delivered as WebVTT to clients without a
            # client-side renderer, so cache both representations in one pass.
            formats = ("ass", "vtt") if codec in {"ass", "ssa"} else ("vtt",)
            tracks.append(EmbeddedSubtitleTrack(ordinal=ordinal, formats=formats))
        ordinal += 1
    return tracks


_EMBEDDED_CACHE_LOCKS: weakref.WeakValueDictionary[Path, LoopAwareLock] = (
    weakref.WeakValueDictionary()
)


def embedded_subtitle_cache_dir(
    cache_root: Path, file_id: str, mtime: float | None, size_bytes: int | None
) -> Path:
    """Return a cache directory keyed by the file revision.

    A changed mtime or size selects a new directory, so re-encoding or replacing
    a video can never serve stale cues.
    """
    stamp = int((mtime or 0.0) * 1000)
    return cache_root / f"{file_id}_{stamp}_{size_bytes or 0}"


def embedded_subtitle_cache_path(cache_dir: Path, ordinal: int, fmt: str) -> Path:
    """Return the cache file for one subtitle stream and output format."""
    return cache_dir / f"{ordinal}.{fmt}"


def build_embedded_subtitle_command(
    video_path: Path | str,
    missing: Sequence[tuple[int, str]],
    output_dir: Path,
    *,
    ffmpeg_binary: str = "ffmpeg",
) -> list[str]:
    """Build one FFmpeg invocation that extracts every requested text track.

    Multiple outputs share a single demux pass, so opening a second subtitle
    track no longer reads the whole file again.
    """
    cmd = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        str(video_path),
        "-copyts",
    ]
    for ordinal, fmt in missing:
        output = output_dir / f"{ordinal}.{fmt}"
        if fmt == "ass":
            # ASS packets can be copied verbatim, preserving styles exactly.
            cmd.extend(["-map", f"0:s:{ordinal}", "-c:s", "copy", "-f", "ass", str(output)])
        else:
            cmd.extend(
                [
                    "-map",
                    f"0:s:{ordinal}",
                    "-c:s",
                    "webvtt",
                    "-f",
                    "webvtt",
                    str(output),
                ]
            )
    return cmd


def _all_cached(cache_dir: Path, wanted: list[tuple[int, str]]) -> bool:
    return all(
        embedded_subtitle_cache_path(cache_dir, ordinal, fmt).is_file() for ordinal, fmt in wanted
    )


def _missing_cached(cache_dir: Path, wanted: list[tuple[int, str]]) -> list[tuple[int, str]]:
    return [
        (ordinal, fmt)
        for ordinal, fmt in wanted
        if not embedded_subtitle_cache_path(cache_dir, ordinal, fmt).is_file()
    ]


def _promote_cached_outputs(
    temp_dir: Path, cache_dir: Path, missing: list[tuple[int, str]]
) -> None:
    for ordinal, fmt in missing:
        source = temp_dir / f"{ordinal}.{fmt}"
        if source.is_file() and source.stat().st_size > 0:
            with contextlib.suppress(OSError):
                source.replace(embedded_subtitle_cache_path(cache_dir, ordinal, fmt))


async def ensure_embedded_subtitles(
    video_path: Path | str,
    tracks: Sequence[EmbeddedSubtitleTrack],
    cache_dir: Path,
    *,
    ffmpeg_binary: str = "ffmpeg",
    timeout: float = EMBEDDED_SUBTITLE_TIMEOUT_SECONDS,
) -> None:
    """Populate ``cache_dir`` with every text subtitle track in one pass.

    The function is idempotent and serializes concurrent callers so a viewer
    opening a track while another request prewarms it does not start a second
    FFmpeg process.
    """
    wanted = sorted({(track.ordinal, fmt) for track in tracks for fmt in track.formats})
    if not wanted:
        return
    if await asyncio.to_thread(_all_cached, cache_dir, wanted):
        return

    lock = _EMBEDDED_CACHE_LOCKS.get(cache_dir)
    if lock is None:
        lock = LoopAwareLock()
        _EMBEDDED_CACHE_LOCKS[cache_dir] = lock
    async with lock:
        missing = await asyncio.to_thread(_missing_cached, cache_dir, wanted)
        if not missing:
            return

        temp_dir = cache_dir / f".tmp-{new_id()}"
        try:
            await asyncio.to_thread(cache_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(temp_dir.mkdir)
        except OSError as exc:
            logger.warning("subtitle cache is not writable (%s): %s", cache_dir, exc)
            return
        try:
            cmd = build_embedded_subtitle_command(
                video_path,
                missing,
                temp_dir,
                ffmpeg_binary=ffmpeg_binary,
            )
            await _run_subtitle_ffmpeg(cmd, timeout=timeout)
            # Move whatever FFmpeg produced: a single unsupported track must not
            # discard the outputs that did succeed.
            await asyncio.to_thread(_promote_cached_outputs, temp_dir, cache_dir, missing)
        finally:
            await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)


async def _run_subtitle_ffmpeg(cmd: list[str], *, timeout: float) -> None:
    """Run a subtitle extraction command, killing it cleanly on timeout."""
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            logger.warning(
                "ffmpeg subtitle extraction failed (exit %d): %s",
                proc.returncode,
                stderr.decode("utf-8", errors="replace")[:1000],
            )
    except TimeoutError:
        if proc is not None and proc.returncode is None:
            proc.kill()
            await proc.wait()
        logger.warning("ffmpeg subtitle extraction timed out after %.0fs", timeout)
    except OSError as exc:
        logger.warning("ffmpeg subtitle extraction error: %s", exc)


async def read_embedded_subtitle(cache_dir: Path, ordinal: int, fmt: str) -> str | None:
    """Read a cached subtitle document, or return ``None`` when absent."""
    path = embedded_subtitle_cache_path(cache_dir, ordinal, fmt)
    if not path.is_file():
        return None
    try:
        return await asyncio.to_thread(path.read_text, "utf-8", "replace")
    except OSError:
        return None


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
    "EMBEDDED_SUBTITLE_TIMEOUT_SECONDS",
    "SUPPORTED_SUFFIXES",
    "EmbeddedSubtitleTrack",
    "SubtitleRef",
    "build_embedded_subtitle_command",
    "convert_to_webvtt",
    "decode_subtitle_bytes",
    "decode_subtitle_id",
    "discover_subtitles",
    "embedded_subtitle_cache_dir",
    "embedded_subtitle_cache_path",
    "encode_subtitle_id",
    "ensure_embedded_subtitles",
    "extract_embedded_subtitle",
    "matches_language",
    "read_embedded_subtitle",
    "shift_webvtt",
    "subtitle_language",
    "text_subtitle_tracks",
]
