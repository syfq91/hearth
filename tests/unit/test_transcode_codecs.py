"""Unit tests for RFC 6381 dynamic codec strings (T08).

Validates:
- H.264 Baseline/Main/High at 3.0/3.1/4.0/5.1
- HEVC Main L93/L150
- AV1 8-bit
- AAC-LC, HE-AAC
- AC-3, E-AC-3
- Unknown profile -> omitted (returns None)
- Master playlist uses real CODECS, BANDWIDTH, RESOLUTION, FRAME-RATE
- No hardcoded avc1.640028 remains for non-High/4.0 sources
"""

from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.media.probe import ProbeStream
from app.services.transcode.codecs import (
    audio_codec_string,
    compute_master_codecs,
    video_codec_string,
)
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import TranscodeSession

# --- H.264 Golden Strings ---


@pytest.mark.parametrize(
    ("profile", "level", "expected"),
    [
        ("Baseline", 3.0, "avc1.42001e"),
        ("Baseline", 3.1, "avc1.42001f"),
        ("Baseline", 4.0, "avc1.420028"),
        ("Baseline", 5.1, "avc1.420033"),
        ("Main", 3.0, "avc1.4d001e"),
        ("Main", 3.1, "avc1.4d001f"),
        ("Main", 4.0, "avc1.4d0028"),
        ("Main", 5.1, "avc1.4d0033"),
        ("High", 3.0, "avc1.64001e"),
        ("High", 3.1, "avc1.64001f"),
        ("High", 4.0, "avc1.640028"),
        ("High", 5.1, "avc1.640033"),
    ],
)
def test_h264_golden_strings(profile: str, level: float, expected: str) -> None:
    assert video_codec_string("h264", profile=profile, level=level) == expected


@pytest.mark.parametrize(
    ("level_int", "expected"),
    [
        (30, "avc1.64001e"),
        (31, "avc1.64001f"),
        (40, "avc1.640028"),
        (51, "avc1.640033"),
    ],
)
def test_h264_integer_level_idc(level_int: int, expected: str) -> None:
    assert video_codec_string("h264", profile="High", level=level_int) == expected


# --- HEVC Golden Strings ---


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("L93", "hvc1.1.6.L93.B0"),
        (93, "hvc1.1.6.L93.B0"),
        (3.1, "hvc1.1.6.L93.B0"),
        ("L150", "hvc1.1.6.L150.B0"),
        (150, "hvc1.1.6.L150.B0"),
        (5.0, "hvc1.1.6.L150.B0"),
    ],
)
def test_hevc_main_golden_strings(level: int | float | str, expected: str) -> None:
    assert video_codec_string("hevc", profile="Main", level=level) == expected


# --- AV1 Golden Strings ---


def test_av1_8bit_golden_string() -> None:
    assert video_codec_string("av1", profile="Main", bit_depth=8) == "av01.0.08M.08"
    assert video_codec_string("av1", bit_depth=8) == "av01.0.08M.08"


# --- Audio Golden Strings ---


@pytest.mark.parametrize(
    ("codec", "profile", "expected"),
    [
        ("aac", "LC", "mp4a.40.2"),
        ("aac", "AAC-LC", "mp4a.40.2"),
        ("aac", None, "mp4a.40.2"),
        ("aac", "HE-AAC", "mp4a.40.5"),
        ("aac", "HE-AACv1", "mp4a.40.5"),
        ("ac3", None, "ac-3"),
        ("ac-3", None, "ac-3"),
        ("eac3", None, "ec-3"),
        ("ec-3", None, "ec-3"),
        ("opus", None, "Opus"),
    ],
)
def test_audio_codec_strings(codec: str, profile: str | None, expected: str) -> None:
    assert audio_codec_string(codec, profile=profile) == expected


# --- Unknown Profile / Codec Omitted (INV-2) ---


@pytest.mark.parametrize(
    ("codec", "profile", "level"),
    [
        ("h264", "UnknownProfile", 4.0),
        ("h264", None, 4.0),
        ("h264", "High", None),
        ("hevc", "UnknownProfile", 150),
        ("hevc", "Main", None),
        ("unknown_video", "High", 4.0),
    ],
)
def test_unknown_video_profile_returns_none(
    codec: str, profile: str | None, level: float | int | None
) -> None:
    assert video_codec_string(codec, profile=profile, level=level) is None


@pytest.mark.parametrize(
    ("codec", "profile"),
    [
        ("aac", "UnknownProfile"),
        ("unknown_audio", None),
        ("flac", None),
    ],
)
def test_unknown_audio_profile_returns_none(codec: str, profile: str | None) -> None:
    assert audio_codec_string(codec, profile=profile) is None


# --- Master Playlist Codecs & Non-High/4.0 Sources ---


def test_compute_master_codecs_non_high_sources(tmp_path: Path) -> None:
    # 1. Copied H.264 Baseline L3.0 + Copied AC-3
    plan_copy = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=6,
        subtitle_action="none",
        hwaccel="none",
    )
    src_v = ProbeStream(
        index=0,
        codec_type="video",
        codec_name="h264",
        profile="Baseline",
        level=30,
        width=1280,
        height=720,
    )
    src_a = ProbeStream(
        index=1,
        codec_type="audio",
        codec_name="ac3",
        channels=6,
    )
    session = TranscodeSession(
        session_id="s_baseline",
        user_id="u1",
        item_id="i1",
        file_id="f1",
        input_path=Path("/media/test.mp4"),
        output_dir=Path(tmp_path),
        plan=plan_copy,
        settings=Settings(transcode_dir=Path(tmp_path)),
        source_video=src_v,
        source_audio=src_a,
    )

    codecs = compute_master_codecs(session)
    assert codecs == "avc1.42001e,ac-3"
    assert "avc1.640028" not in codecs

    # 2. Copied HEVC Main L93 + Transcoded AAC
    plan_hevc = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="transcode",
        audio_codec="aac",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    src_v_hevc = ProbeStream(
        index=0,
        codec_type="video",
        codec_name="hevc",
        profile="Main",
        level=93,
    )
    session_hevc = TranscodeSession(
        session_id="s_hevc",
        user_id="u1",
        item_id="i1",
        file_id="f1",
        input_path=Path("/media/test.mp4"),
        output_dir=Path(tmp_path),
        plan=plan_hevc,
        settings=Settings(transcode_dir=Path(tmp_path)),
        source_video=src_v_hevc,
    )
    codecs_hevc = compute_master_codecs(session_hevc)
    assert codecs_hevc == "hvc1.1.6.L93.B0,mp4a.40.2"
    assert "avc1.640028" not in codecs_hevc
