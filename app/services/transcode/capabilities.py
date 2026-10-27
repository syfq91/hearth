"""Hardware transcode capability detection and verification.

Probes the render device, parses vainfo profiles, tests actual 1-second encodes
via ffmpeg for supported codecs, and caches the result for playback decisions
and admin diagnostics.
"""

from __future__ import annotations

import asyncio
import re

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.system import _run

_VAAPI_DRIVER_RE = re.compile(r"Driver version:\s*(.+)")
_PROFILE_LINE_RE = re.compile(r"\s*(VAProfile\w+)\s*:\s*(VAEntrypoint\w+)")

_CODEC_MAP: dict[str, str] = {
    "H264": "h264",
    "HEVC": "hevc",
    "AV1": "av1",
    "VP9": "vp9",
    "VP8": "vp8",
    "MPEG2": "mpeg2",
    "VC1": "vc1",
}

_FFMPEG_ENCODER_NAMES: dict[str, str] = {
    "h264": "h264_vaapi",
    "hevc": "hevc_vaapi",
    "av1": "av1_vaapi",
    "vp9": "vp9_vaapi",
}


class CodecCapability(BaseModel):
    """Capability for a single media codec."""

    codec: str
    decode: bool = False
    encode: bool = False
    test_encode_ok: bool = False
    test_encode_error: str | None = None


class ServerTranscodeCapabilities(BaseModel):
    """Aggregated hardware and software transcoding capabilities."""

    render_device: str
    available: bool = False
    force_software: bool = False
    driver_version: str | None = None
    codecs: dict[str, CodecCapability] = Field(default_factory=dict)
    tonemap_vaapi: bool = False
    software_encoders: list[str] = Field(default_factory=lambda: ["libx264", "aac"])
    error: str | None = None

    def can_vaapi_encode(self, codec: str) -> bool:
        """Return True if hardware encoding is supported and verified for codec."""
        if self.force_software or not self.available:
            return False
        cap = self.codecs.get(codec.lower())
        return bool(cap and cap.encode and cap.test_encode_ok)

    def can_vaapi_decode(self, codec: str) -> bool:
        """Return True if hardware decoding is supported for codec."""
        if self.force_software or not self.available:
            return False
        cap = self.codecs.get(codec.lower())
        return bool(cap and cap.decode)


def parse_vainfo_output(stdout: str) -> dict[str, tuple[bool, bool]]:
    """Parse vainfo output into {codec: (can_decode, can_encode)}.

    Entrypoint VLD indicates decode capability.
    Entrypoint EncSlice / EncSliceLP indicates encode capability.
    """
    codecs_raw: dict[str, set[str]] = {}
    for line in stdout.splitlines():
        match = _PROFILE_LINE_RE.match(line)
        if not match:
            continue
        profile, entrypoint = match.group(1), match.group(2)
        # Find which codec family this profile belongs to
        for key, codec_name in _CODEC_MAP.items():
            if key in profile:
                codecs_raw.setdefault(codec_name, set()).add(entrypoint)
                break

    result: dict[str, tuple[bool, bool]] = {}
    for codec, entrypoints in codecs_raw.items():
        decode = "VAEntrypointVLD" in entrypoints
        encode = bool(entrypoints & {"VAEntrypointEncSlice", "VAEntrypointEncSliceLP"})
        result[codec] = (decode, encode)
    return result


async def _check_tonemap_vaapi(ffmpeg_binary: str, timeout: float = 3.0) -> bool:
    """Check if tonemap_vaapi filter is compiled into ffmpeg."""
    res = await _run([ffmpeg_binary, "-filters"], timeout=timeout)
    if res is None:
        return False
    returncode, stdout, _ = res
    return returncode == 0 and "tonemap_vaapi" in stdout


async def _run_test_encode(
    ffmpeg_binary: str,
    device: str,
    encoder_name: str,
    timeout: float = 5.0,
) -> tuple[bool, str | None]:
    """Execute a 1-second null encode to verify the hardware encoder actually works."""
    cmd = [
        ffmpeg_binary,
        "-loglevel",
        "error",
        "-init_hw_device",
        f"vaapi=va:{device}",
        "-f",
        "lavfi",
        "-i",
        "testsrc=d=1:s=1280x720:r=24",
        "-vf",
        "format=nv12,hwupload",
        "-c:v",
        encoder_name,
        "-f",
        "null",
        "-",
    ]
    res = await _run(cmd, timeout=timeout)
    if res is None:
        return False, f"ffmpeg timed out after {timeout:g}s"
    returncode, stdout, stderr = res
    if returncode != 0:
        err = (stderr or stdout).strip().splitlines()
        msg = err[0] if err else f"ffmpeg exited with {returncode}"
        return False, msg
    return True, None


_cached_capabilities: ServerTranscodeCapabilities | None = None
_cache_lock = asyncio.Lock()


async def probe_transcode_capabilities(
    settings: Settings,
    force_refresh: bool = False,
) -> ServerTranscodeCapabilities:
    """Probe system VAAPI capability and test hardware encoders."""
    global _cached_capabilities

    async with _cache_lock:
        if _cached_capabilities is not None and not force_refresh:
            return _cached_capabilities

        device_path = settings.render_device
        device_label = str(device_path)
        force_software = settings.transcode_force_software

        if force_software:
            caps = ServerTranscodeCapabilities(
                render_device=device_label,
                available=False,
                force_software=True,
                error="forced software mode",
            )
            _cached_capabilities = caps
            return caps

        if not device_path.exists():
            caps = ServerTranscodeCapabilities(
                render_device=device_label,
                available=False,
                error="render device not present",
            )
            _cached_capabilities = caps
            return caps

        # Run vainfo
        vainfo_res = await _run(
            [settings.vainfo_binary, "--display", "drm", "--device", device_label],
            timeout=5.0,
        )
        if vainfo_res is None or vainfo_res[0] != 0 or "VAProfile" not in vainfo_res[1]:
            err = "vainfo unavailable or failed"
            if vainfo_res and (vainfo_res[2] or vainfo_res[1]):
                lines = (vainfo_res[2] or vainfo_res[1]).strip().splitlines()
                err = lines[-1] if lines else err
            caps = ServerTranscodeCapabilities(
                render_device=device_label,
                available=False,
                error=err,
            )
            _cached_capabilities = caps
            return caps

        _, stdout, _ = vainfo_res
        driver_match = _VAAPI_DRIVER_RE.search(stdout)
        driver_version = driver_match.group(1).strip() if driver_match else None

        parsed_codecs = parse_vainfo_output(stdout)
        codecs: dict[str, CodecCapability] = {}

        # For any codec that vainfo reports encode, run a test encode
        for codec_name, (decode, encode) in parsed_codecs.items():
            test_ok = False
            test_err = None
            if encode and codec_name in _FFMPEG_ENCODER_NAMES:
                encoder = _FFMPEG_ENCODER_NAMES[codec_name]
                test_ok, test_err = await _run_test_encode(
                    settings.ffmpeg_binary,
                    device_label,
                    encoder,
                    timeout=settings.transcode_test_encode_timeout_seconds,
                )
            codecs[codec_name] = CodecCapability(
                codec=codec_name,
                decode=decode,
                encode=encode,
                test_encode_ok=test_ok,
                test_encode_error=test_err,
            )

        tonemap = await _check_tonemap_vaapi(settings.ffmpeg_binary)

        caps = ServerTranscodeCapabilities(
            render_device=device_label,
            available=True,
            driver_version=driver_version,
            codecs=codecs,
            tonemap_vaapi=tonemap,
        )
        _cached_capabilities = caps
        return caps


def reset_transcode_capabilities_cache() -> None:
    """Clear cached capabilities for test isolation."""
    global _cached_capabilities
    _cached_capabilities = None


__all__ = [
    "CodecCapability",
    "ServerTranscodeCapabilities",
    "parse_vainfo_output",
    "probe_transcode_capabilities",
    "reset_transcode_capabilities_cache",
]
