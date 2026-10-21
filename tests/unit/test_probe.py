"""Unit tests for the ffprobe wrapper and its payload parser."""

import sys
from pathlib import Path
from typing import Any

import pytest

from app.services.media.probe import ProbeError, parse_probe_payload, probe_media

PAYLOAD: dict[str, Any] = {
    "format": {
        "format_name": "matroska,webm",
        "format_long_name": "Matroska / WebM",
        "duration": "7200.500000",
        "bit_rate": "18000000",
        "size": "16200000000",
    },
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "hevc",
            "codec_long_name": "H.265 / HEVC",
            "profile": "Main 10",
            "level": 153,
            "width": 3840,
            "height": 2160,
            "pix_fmt": "yuv420p10le",
            "bit_rate": "15000000",
            "color_transfer": "smpte2084",
            "codec_tag_string": "hvc1",
            "side_data_list": [{"side_data_type": "Mastering display metadata"}],
            "disposition": {"default": 1, "forced": 0},
            "tags": {"language": "eng", "title": "Main"},
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "eac3",
            "channels": 6,
            "channel_layout": "5.1(side)",
            "sample_rate": "48000",
            "bit_rate": "640000",
            "disposition": {"default": 1},
            "tags": {"language": "eng"},
        },
        {
            "index": 2,
            "codec_type": "subtitle",
            "codec_name": "subrip",
            "disposition": {"default": 0, "forced": 1},
            "tags": {"language": "spa", "title": "Forced"},
        },
        {
            "index": 3,
            "codec_type": "video",
            "codec_name": "mjpeg",
            "width": 600,
            "height": 600,
            "disposition": {"default": 0},
        },
    ],
    "chapters": [
        {"start_time": "0.000000", "end_time": "60.000000", "tags": {"title": "Opening"}},
        {"start_time": "60.000000", "end_time": "120.000000", "tags": {}},
    ],
}


def test_parse_payload_maps_format_and_streams() -> None:
    result = parse_probe_payload(PAYLOAD)

    assert result.container == "matroska,webm"
    assert result.duration_seconds == pytest.approx(7200.5)
    assert result.bit_rate == 18_000_000
    assert result.size_bytes == 16_200_000_000
    assert len(result.streams) == 4
    assert [stream.index for stream in result.video_streams] == [0, 3]
    assert len(result.audio_streams) == 1
    assert len(result.subtitle_streams) == 1


def test_parse_payload_detects_hdr_and_audio_details() -> None:
    result = parse_probe_payload(PAYLOAD)

    video = result.primary_video
    assert video is not None
    assert video.width == 3840
    assert video.height == 2160
    assert video.pix_fmt == "yuv420p10le"
    assert video.is_hdr is True
    assert video.hdr_format == "HDR10"
    assert video.color_transfer == "smpte2084"
    assert video.is_default is True

    audio = result.audio_streams[0]
    assert audio.channels == 6
    assert audio.channel_layout == "5.1(side)"
    assert audio.sample_rate == 48000
    assert audio.language == "eng"

    subtitle = result.subtitle_streams[0]
    assert subtitle.is_forced is True
    assert subtitle.is_default is False
    assert subtitle.language == "spa"


def test_parse_payload_detects_dolby_vision_tag() -> None:
    payload = {
        "format": {"format_name": "mp4"},
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "codec_tag_string": "dvh1",
                "disposition": {},
            }
        ],
    }

    result = parse_probe_payload(payload)

    assert result.primary_video is not None
    assert result.primary_video.hdr_format == "Dolby Vision"


def test_parse_payload_chapters_and_missing_fields() -> None:
    result = parse_probe_payload(PAYLOAD)

    assert [chapter.title for chapter in result.chapters] == ["Opening", None]
    assert result.chapters[0].start_seconds == 0.0
    assert result.chapters[1].end_seconds == 120.0

    minimal = parse_probe_payload({"format": {}, "streams": [], "chapters": []})
    assert minimal.container == "unknown"
    assert minimal.duration_seconds is None
    assert minimal.primary_video is None


def test_parse_payload_rejects_malformed_lists() -> None:
    with pytest.raises(ValueError):
        parse_probe_payload({"format": {}, "streams": {"bad": 1}, "chapters": []})


async def test_probe_media_runs_ffprobe_and_parses_output(tmp_path: Path) -> None:
    import json

    script = _write_script(tmp_path, "fake_ffprobe", json.dumps(PAYLOAD))
    media = tmp_path / "movie.mkv"
    media.touch()

    result = await probe_media(media, ffprobe_binary=str(script), timeout=5)

    assert result.container == "matroska,webm"
    assert result.primary_video is not None


async def test_probe_media_reports_missing_binary(tmp_path: Path) -> None:
    with pytest.raises(ProbeError, match="not installed"):
        await probe_media(tmp_path / "movie.mkv", ffprobe_binary="hearth-missing-ffprobe")


async def test_probe_media_reports_nonzero_exit(tmp_path: Path) -> None:
    script = _write_script(
        tmp_path, "failing_ffprobe", "Invalid data found when processing input", 1
    )

    with pytest.raises(ProbeError, match="Invalid data"):
        await probe_media(tmp_path / "bad.mkv", ffprobe_binary=str(script))


async def test_probe_media_reports_invalid_json(tmp_path: Path) -> None:
    script = _write_script(tmp_path, "bad_ffprobe", "this is not json")

    with pytest.raises(ProbeError, match="invalid ffprobe output"):
        await probe_media(tmp_path / "bad.mkv", ffprobe_binary=str(script))


async def test_probe_media_times_out(tmp_path: Path) -> None:
    script = _write_script(tmp_path, "slow_ffprobe", "{}", sleep=5)

    with pytest.raises(ProbeError, match="timed out"):
        await probe_media(tmp_path / "slow.mkv", ffprobe_binary=str(script), timeout=0.1)


def _write_script(
    tmp_path: Path,
    name: str,
    output: str,
    exit_code: int = 0,
    *,
    sleep: float = 0,
) -> Path:
    """Create an executable Python script that mimics ffprobe."""
    script = tmp_path / name
    lines = [f"#!{sys.executable}", "import sys"]
    if sleep:
        lines.append(f"import time; time.sleep({sleep})")
    target = "sys.stderr" if exit_code else "sys.stdout"
    lines.append(f"print({output!r}, file={target})")
    lines.append(f"raise SystemExit({exit_code})")
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script
