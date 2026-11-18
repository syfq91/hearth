"""Unit tests for user-facing quality ceilings.

The labels are mapped to resolution/bitrate ceilings in one place and folded
into the resolved client capabilities before the pure decision engine runs.
"""

import pytest
from pydantic import ValidationError

from app.api.v1.playback import PlaybackInfoRequest
from app.services.media.probe import ProbeResult, ProbeStream
from app.services.transcode.capabilities import ServerTranscodeCapabilities
from app.services.transcode.decision import (
    PlaybackOptions,
    TranscodeReason,
    UserLimits,
    evaluate_playback_plan,
)
from app.services.transcode.profile import (
    BUILTIN_PRESETS,
    QUALITY_LIMITS,
    ClientCapabilities,
    apply_quality_ceiling,
)


def _software_server() -> ServerTranscodeCapabilities:
    return ServerTranscodeCapabilities(
        render_device="/dev/dri/renderD128",
        force_software=True,
    )


def _probe(width: int = 3840, height: int = 2160, bit_rate: int = 8_000_000) -> ProbeResult:
    return ProbeResult(
        container="mp4",
        bit_rate=bit_rate + 192_000,
        duration_seconds=120.0,
        streams=[
            ProbeStream(
                index=0,
                codec_type="video",
                codec_name="h264",
                profile="High",
                width=width,
                height=height,
                bit_rate=bit_rate,
            ),
            ProbeStream(
                index=1,
                codec_type="audio",
                codec_name="aac",
                channels=2,
                bit_rate=192_000,
                is_default=True,
            ),
        ],
    )


def _client() -> ClientCapabilities:
    return ClientCapabilities(
        containers=["mp4"],
        video_codecs=["h264"],
        audio_codecs=["aac"],
        max_width=3840,
        max_height=2160,
    )


@pytest.mark.parametrize(
    ("quality", "height", "bitrate"),
    [
        ("auto", None, None),
        ("sd", 480, 1_500_000),
        ("hd", 720, 3_000_000),
        ("fhd", 1080, 6_000_000),
        ("4k", 2160, 20_000_000),
    ],
)
def test_quality_limit_ladder(quality: str, height: int | None, bitrate: int | None) -> None:
    limit = QUALITY_LIMITS[quality]
    assert limit.max_height == height
    assert limit.max_bitrate == bitrate


def test_auto_applies_no_ceiling() -> None:
    caps = _client()
    apply_quality_ceiling(caps, "auto")
    assert caps.max_height == 2160
    assert caps.max_bitrate is None


def test_quality_ceiling_intersects_device_ceiling() -> None:
    caps = ClientCapabilities(max_height=720, max_bitrate=3_000_000)
    apply_quality_ceiling(caps, "fhd")
    assert caps.max_height == 720
    assert caps.max_bitrate == 3_000_000

    apply_quality_ceiling(caps, "sd")
    assert caps.max_height == 480
    assert caps.max_bitrate == 1_500_000


@pytest.mark.parametrize(
    ("quality", "expected_height", "expected_mode"),
    [
        ("sd", 480, "transcode_hls"),
        ("hd", 720, "transcode_hls"),
        ("fhd", 1080, "transcode_hls"),
        ("4k", None, "direct_play"),
        ("auto", None, "direct_play"),
    ],
)
def test_quality_ceiling_shapes_plan(
    quality: str, expected_height: int | None, expected_mode: str
) -> None:
    plan = evaluate_playback_plan(
        _probe(),
        _client(),
        UserLimits(),
        _software_server(),
        PlaybackOptions(quality=quality),  # type: ignore[arg-type]
    )
    assert plan.mode == expected_mode, plan.reasons
    assert plan.target_height == expected_height
    if expected_height is not None:
        assert TranscodeReason.VideoResolutionNotSupported in plan.reasons
    else:
        assert TranscodeReason.VideoResolutionNotSupported not in plan.reasons


def test_ceiling_above_source_still_direct_plays() -> None:
    plan = evaluate_playback_plan(
        _probe(width=1280, height=720, bit_rate=3_000_000),
        _client(),
        UserLimits(),
        _software_server(),
        PlaybackOptions(quality="4k"),
    )
    assert plan.mode == "direct_play"
    assert plan.target_height is None


def test_quality_ceiling_intersects_user_bitrate_limit() -> None:
    plan = evaluate_playback_plan(
        _probe(bit_rate=8_000_000),
        _client(),
        UserLimits(max_bitrate=1_000_000),
        _software_server(),
        PlaybackOptions(quality="fhd"),
    )
    assert plan.mode == "transcode_hls"
    assert plan.target_bitrate == 1_000_000


def test_preset_and_ceiling_are_independent() -> None:
    """The shared preset object must not be mutated by a per-request ceiling."""
    preset = BUILTIN_PRESETS["chrome"]
    plan = evaluate_playback_plan(
        _probe(),
        preset,
        UserLimits(),
        _software_server(),
        PlaybackOptions(quality="sd"),
    )
    assert plan.target_height == 480
    assert preset.max_height == 2160


def test_unknown_quality_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PlaybackInfoRequest(quality="ultra")  # type: ignore[arg-type]
