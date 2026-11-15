"""Hardware transcode capability detection and verification.

Probes the render device, parses vainfo profiles, tests actual 1-second encodes
via ffmpeg for supported codecs, and caches the result for playback decisions
and admin diagnostics.

Probe timeouts are not conclusive: a slow iGPU can take several seconds to
initialise, and treating one slow probe as "no hardware encoder" silently
demotes every later session to software encoding for the life of the process.
Inconclusive probes are therefore flagged and re-run in the background.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import NamedTuple

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.system import _run, run_command

logger = logging.getLogger("hearth.transcode.capabilities")

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
    # True when the probe timed out instead of conclusively reporting that the
    # device or an encoder is unavailable. The result is still used for the
    # current decisions, but it is re-probed in the background.
    probe_incomplete: bool = False

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
        normalized = codec.lower()
        if normalized == "mpeg2video":
            normalized = "mpeg2"
        elif normalized in ("h265", "hev1", "hvc1"):
            normalized = "hevc"
        elif normalized in ("avc", "avc1"):
            normalized = "h264"
        cap = self.codecs.get(normalized)
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


class TestEncodeResult(NamedTuple):
    """Outcome of a hardware encoder smoke test."""

    ok: bool
    timed_out: bool
    error: str | None


async def _run_test_encode(
    ffmpeg_binary: str,
    device: str,
    encoder_name: str,
    timeout: float = 5.0,
) -> TestEncodeResult:
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
    outcome = await run_command(cmd, timeout=timeout)
    if outcome.state == "timeout":
        return TestEncodeResult(False, True, f"ffmpeg timed out after {timeout:g}s")
    if outcome.state == "missing":
        return TestEncodeResult(False, False, f"{ffmpeg_binary} not found")
    if outcome.returncode != 0:
        err = (outcome.stderr or outcome.stdout).strip().splitlines()
        msg = err[0] if err else f"ffmpeg exited with {outcome.returncode}"
        return TestEncodeResult(False, False, msg)
    return TestEncodeResult(True, False, None)


_cached_capabilities: ServerTranscodeCapabilities | None = None
_cache_lock = asyncio.Lock()
_retry_task: asyncio.Task[None] | None = None
_retry_attempt = 0


async def _probe_once(
    settings: Settings,
) -> tuple[ServerTranscodeCapabilities, bool]:
    """Probe the render device.

    Returns the capabilities and whether the probe was inconclusive because a
    step timed out. Only conclusive results are safe to remember indefinitely.
    """
    device_path = settings.render_device
    device_label = str(device_path)

    if settings.transcode_force_software:
        return (
            ServerTranscodeCapabilities(
                render_device=device_label,
                available=False,
                force_software=True,
                error="forced software mode",
            ),
            False,
        )

    if not device_path.exists():
        return (
            ServerTranscodeCapabilities(
                render_device=device_label,
                available=False,
                error="render device not present",
            ),
            False,
        )

    # Run vainfo
    vainfo_timeout = settings.transcode_vainfo_timeout_seconds
    vainfo_res = await run_command(
        [settings.vainfo_binary, "--display", "drm", "--device", device_label],
        timeout=vainfo_timeout,
    )
    if vainfo_res.state == "timeout":
        logger.warning(
            "vainfo timed out after %gs; treating hardware encoding as unavailable "
            "and retrying the probe in the background",
            vainfo_timeout,
            extra={"render_device": device_label, "event": "capability_probe_timeout"},
        )
        return (
            ServerTranscodeCapabilities(
                render_device=device_label,
                available=False,
                error="vainfo timed out; probe will retry",
                probe_incomplete=True,
            ),
            True,
        )
    if (
        vainfo_res.state == "missing"
        or vainfo_res.returncode != 0
        or "VAProfile" not in vainfo_res.stdout
    ):
        err = "vainfo unavailable or failed"
        if vainfo_res.state == "ok" and (vainfo_res.stderr or vainfo_res.stdout):
            lines = (vainfo_res.stderr or vainfo_res.stdout).strip().splitlines()
            err = lines[-1] if lines else err
        return (
            ServerTranscodeCapabilities(
                render_device=device_label,
                available=False,
                error=err,
            ),
            False,
        )

    stdout = vainfo_res.stdout
    driver_match = _VAAPI_DRIVER_RE.search(stdout)
    driver_version = driver_match.group(1).strip() if driver_match else None

    parsed_codecs = parse_vainfo_output(stdout)
    codecs: dict[str, CodecCapability] = {}
    incomplete = False

    # For any codec that vainfo reports encode, run a test encode. The smoke
    # tests are independent, so run them together: the driver initialises once
    # and the probe does not take four deadline windows in a row.
    encoders = {
        codec_name: _FFMPEG_ENCODER_NAMES[codec_name]
        for codec_name, (_decode, encode) in parsed_codecs.items()
        if encode and codec_name in _FFMPEG_ENCODER_NAMES
    }
    test_results = await asyncio.gather(
        *(
            _run_test_encode(
                settings.ffmpeg_binary,
                device_label,
                encoder,
                timeout=settings.transcode_test_encode_timeout_seconds,
            )
            for encoder in encoders.values()
        )
    )
    results_by_codec = dict(zip(encoders, test_results, strict=True))

    for codec_name, (decode, encode) in parsed_codecs.items():
        result = results_by_codec.get(codec_name)
        if result is not None and result.timed_out:
            incomplete = True
        codecs[codec_name] = CodecCapability(
            codec=codec_name,
            decode=decode,
            encode=encode,
            test_encode_ok=bool(result and result.ok),
            test_encode_error=result.error if result else None,
        )

    tonemap = await _check_tonemap_vaapi(settings.ffmpeg_binary)

    caps = ServerTranscodeCapabilities(
        render_device=device_label,
        available=True,
        driver_version=driver_version,
        codecs=codecs,
        tonemap_vaapi=tonemap,
        probe_incomplete=incomplete,
    )
    return caps, incomplete


async def probe_transcode_capabilities(
    settings: Settings,
    force_refresh: bool = False,
) -> ServerTranscodeCapabilities:
    """Probe system VAAPI capability and test hardware encoders.

    Caches the result. A probe that timed out is cached but flagged, and a
    background retry replaces it as soon as the device answers, so a transient
    boot-time timeout cannot permanently demote the process to software
    encoding.
    """
    global _cached_capabilities

    async with _cache_lock:
        if _cached_capabilities is not None and not force_refresh:
            cached = _cached_capabilities
            if cached.probe_incomplete:
                _schedule_probe_retry(settings)
            return cached

        try:
            async with asyncio.timeout(settings.transcode_capability_probe_timeout_seconds):
                caps, incomplete = await _probe_once(settings)
        except TimeoutError:
            logger.warning(
                "transcode capability probe exceeded %gs; retrying in the background",
                settings.transcode_capability_probe_timeout_seconds,
                extra={"event": "capability_probe_budget_exceeded"},
            )
            caps = ServerTranscodeCapabilities(
                render_device=str(settings.render_device),
                available=False,
                error="capability probe timed out; probe will retry",
                probe_incomplete=True,
            )
            incomplete = True
        _cached_capabilities = caps
        if incomplete:
            _schedule_probe_retry(settings)
        else:
            _reset_retry_state()
        return caps


def _schedule_probe_retry(settings: Settings) -> None:
    """Start a bounded background retry when the cached probe was inconclusive."""
    global _retry_task

    if settings.transcode_capability_probe_retries <= 0:
        return
    if _retry_task is not None and not _retry_task.done():
        return
    if _retry_attempt >= settings.transcode_capability_probe_retries:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _retry_task = loop.create_task(
        _probe_retry_loop(settings), name="hearth-capability-probe-retry"
    )


async def _probe_retry_loop(settings: Settings) -> None:
    """Re-probe with linear backoff until conclusive or out of attempts."""
    global _retry_attempt

    total = settings.transcode_capability_probe_retries
    for attempt in range(1, total + 1):
        _retry_attempt = attempt
        await asyncio.sleep(settings.transcode_capability_probe_retry_delay_seconds * attempt)
        try:
            caps = await probe_transcode_capabilities(settings, force_refresh=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("transcode capability probe retry failed")
            continue
        if not caps.probe_incomplete:
            logger.info(
                "transcode capability probe recovered on retry %d",
                attempt,
                extra={"event": "capability_probe_recovered", "attempt": attempt},
            )
            _retry_attempt = 0
            return

    logger.warning(
        "transcode capability probe still inconclusive after %d retries; "
        "hardware encoding stays disabled until the API restarts",
        total,
        extra={"event": "capability_probe_exhausted", "attempts": total},
    )


def _reset_retry_state() -> None:
    global _retry_attempt
    _retry_attempt = 0


def reset_transcode_capabilities_cache() -> None:
    """Clear cached capabilities and pending retries for test isolation."""
    global _cached_capabilities, _retry_task, _retry_attempt

    if _retry_task is not None and not _retry_task.done():
        _retry_task.cancel()
    _retry_task = None
    _retry_attempt = 0
    _cached_capabilities = None


__all__ = [
    "CodecCapability",
    "ServerTranscodeCapabilities",
    "parse_vainfo_output",
    "probe_transcode_capabilities",
    "reset_transcode_capabilities_cache",
]
