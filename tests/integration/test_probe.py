"""Integration test against a real ffmpeg-generated file."""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.media.probe import probe_media

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)


async def test_probe_real_generated_media(tmp_path: Path) -> None:
    media = tmp_path / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=d=1:s=160x120",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-y",
            str(media),
        ],
        check=True,
        capture_output=True,
        timeout=30,
    )

    result = await probe_media(media)

    assert "mp4" in result.container
    assert result.duration_seconds == pytest.approx(1.0, abs=0.25)
    assert result.primary_video is not None
    assert result.primary_video.codec_name == "h264"
    assert result.primary_video.width == 160
    assert result.primary_video.height == 120
    assert result.audio_streams
    assert result.audio_streams[0].codec_name == "aac"
    assert result.size_bytes == media.stat().st_size
