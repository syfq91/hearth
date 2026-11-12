"""Integration and smoke tests for forced keyframes at segment boundaries (T02).

Validates that when video is transcoded:
1. Each produced HLS segment begins with a keyframe (asserted via ffprobe packet flags).
2. Segment durations are within 50 ms of the target segment duration (last segment excepted).
"""

from __future__ import annotations

import asyncio
import re
import shutil
from pathlib import Path

import pytest

from app.services.transcode.builder import build_ffmpeg_hls_command
from app.services.transcode.decision import TranscodePlan

_EXTINF_RE = re.compile(r"^#EXTINF:([\d.]+),")


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe required for keyframe smoke test",
)
@pytest.mark.asyncio
async def test_forced_keyframes_smoke(tmp_path: Path) -> None:
    """Smoke test: ffprobe on produced segments shows each segment begins with a keyframe
    and durations in media.m3u8 are within 50 ms of segment_duration (last segment excepted).
    """
    input_file = tmp_path / "test_source.mp4"
    out_dir = tmp_path / "hls_keyframe_out"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create a 5-second fixture without frequent source keyframes (GOP 120 frames = 5 seconds)
    gen_proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=320x240:rate=24",
        "-t",
        "5",
        "-c:v",
        "libx264",
        "-g",
        "120",
        "-pix_fmt",
        "yuv420p",
        str(input_file),
    )
    await gen_proc.wait()
    assert gen_proc.returncode == 0, "Failed to create input fixture"

    # 2. Transcode with 1-second segment duration using builder
    target_seg_s = 1
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        input_file,
        out_dir,
        segment_duration=target_seg_s,
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err_bytes = await proc.communicate()
    assert proc.returncode == 0, f"FFmpeg transcoding failed:\n{err_bytes.decode()}"

    # 3. Read media.m3u8 and parse segment durations
    m3u8_path = out_dir / "media.m3u8"
    assert m3u8_path.exists(), "media.m3u8 playlist missing"
    m3u8_lines = m3u8_path.read_text(encoding="utf-8").splitlines()

    segment_durations: list[float] = []
    for line in m3u8_lines:
        match = _EXTINF_RE.match(line)
        if match:
            segment_durations.append(float(match.group(1)))

    assert len(segment_durations) >= 4, (
        f"Expected at least 4 segments, got {len(segment_durations)}"
    )

    # Durations within 50 ms (0.05s) of target_seg_s for all except the last segment
    for i, duration in enumerate(segment_durations[:-1]):
        diff = abs(duration - float(target_seg_s))
        assert diff <= 0.05, (
            f"Segment {i} duration {duration}s deviates by {diff:.3f}s "
            f"(> 0.050s) from {target_seg_s}s"
        )

    # 4. Probe each segment: verify first video packet starts with a keyframe
    init_bytes = (out_dir / "init.mp4").read_bytes()
    segment_files = sorted(out_dir.glob("seg_*.m4s"))
    assert len(segment_files) == len(segment_durations)

    for seg_file in segment_files:
        seg_bytes = seg_file.read_bytes()
        combined = init_bytes + seg_bytes

        probe_proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "packet=pts_time,flags",
            "-of",
            "csv=p=0",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await probe_proc.communicate(input=combined)
        assert probe_proc.returncode == 0, f"ffprobe failed for {seg_file.name}:\n{err.decode()}"

        lines = [line.strip() for line in out.decode().splitlines() if line.strip()]
        assert len(lines) > 0, f"No packets found for {seg_file.name}"

        # First packet format from csv: "<pts_time>,<flags>"
        first_packet = lines[0]
        pts_time, flags = first_packet.split(",")
        assert "K" in flags, (
            f"Segment {seg_file.name} first packet does not start with keyframe! "
            f"pts={pts_time}, flags={flags}"
        )
