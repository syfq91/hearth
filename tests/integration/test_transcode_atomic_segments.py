"""Integration and smoke tests for atomic segment writes (T01).

Validates that ffmpeg writes segments to temporary files (*.tmp) and atomically
renames them upon completion, ensuring clients never observe partially written
segments (INV-5).
"""

from __future__ import annotations

import asyncio
import shutil
import threading
import time
from pathlib import Path

import pytest

from app.services.transcode.builder import build_ffmpeg_hls_command
from app.services.transcode.decision import TranscodePlan


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg is required for atomic segment smoke test",
)
@pytest.mark.asyncio
async def test_atomic_segment_writes_smoke(tmp_path: Path) -> None:
    """Verify that during ffmpeg HLS generation:
    1. Segments and playlists are initially written to .tmp files.
    2. No .m4s or .ts file is ever observed at a partial size (smaller than final size).
    3. Final output files exist and no .tmp files remain.
    """
    input_file = tmp_path / "source.mp4"
    out_dir = tmp_path / "hls_output"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Create a 3-second test fixture with keyframes
    gen_proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=640x480:rate=24",
        "-t",
        "3",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        str(input_file),
    )
    await gen_proc.wait()
    assert gen_proc.returncode == 0, "Failed to generate test fixture"

    # 2. Build transcode command (software transcode with slow preset for poll observations)
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
        segment_duration=1,
    )

    # Pacing: adjust preset and enable debug log to trace atomic temp_file usage
    if "-preset" in cmd:
        cmd[cmd.index("-preset") + 1] = "slow"
    if "-loglevel" in cmd:
        cmd[cmd.index("-loglevel") + 1] = "debug"

    # Poller thread to watch filesystem while ffmpeg runs
    stop_event = threading.Event()
    observations: list[tuple[str, int]] = []

    def _poll() -> None:
        while not stop_event.is_set():
            if out_dir.exists():
                try:
                    for entry in out_dir.iterdir():
                        try:
                            size = entry.stat().st_size
                            observations.append((entry.name, size))
                        except FileNotFoundError:
                            continue
                except FileNotFoundError:
                    pass
            time.sleep(0.001)

    poller_thread = threading.Thread(target=_poll, daemon=True)
    poller_thread.start()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()
    stop_event.set()
    poller_thread.join(timeout=2.0)

    assert proc.returncode == 0, "Transcode process failed"
    stderr_text = stderr_bytes.decode(errors="replace")

    # 3. Assertions
    # Verify ffmpeg opened temporary files with .tmp suffix for both segments and playlist
    tmp_opens = [line for line in stderr_text.splitlines() if "Opening" in line and ".tmp" in line]
    assert any("seg_" in line and ".m4s.tmp" in line for line in tmp_opens), (
        f"Expected ffmpeg to open seg_*.m4s.tmp in stderr, got: {tmp_opens}"
    )
    assert any("media.m3u8.tmp" in line for line in tmp_opens), (
        f"Expected ffmpeg to open media.m3u8.tmp in stderr, got: {tmp_opens}"
    )

    # Any temp file observed on filesystem had .tmp suffix
    for name, _ in observations:
        if ".tmp" in name:
            assert name.endswith(".tmp")

    # Check that final files exist and no .tmp files remain on disk after completion
    final_files = [p.name for p in out_dir.iterdir()]
    assert any(name.endswith(".m4s") for name in final_files), (
        "Expected .m4s segments in output directory"
    )
    assert "init.mp4" in final_files, "Expected init.mp4 in output directory"
    assert "media.m3u8" in final_files, "Expected media.m3u8 in output directory"
    assert not any(name.endswith(".tmp") for name in final_files), (
        f"Leftover .tmp files found: {final_files}"
    )

    # Verify that while ffmpeg was running, no .m4s or .ts file was observed at a partial size
    for final_file in final_files:
        if final_file.endswith(".m4s") or final_file.endswith(".ts"):
            final_size = (out_dir / final_file).stat().st_size
            observed_sizes = [size for name, size in observations if name == final_file]
            assert len(observed_sizes) > 0, f"Expected observations for {final_file}"
            for observed_size in observed_sizes:
                assert observed_size == final_size, (
                    f"File {final_file} observed with partial size {observed_size} "
                    f"(final: {final_size})"
                )
