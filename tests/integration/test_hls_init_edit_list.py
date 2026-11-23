"""Regression test for fMP4 edit lists in resumed HLS sessions.

ffmpeg encodes a mid-file ``-ss`` (and ``-output_ts_offset``) as an empty
``edts/elst`` in init.mp4. Chromium ignores that edit list, but Firefox's MSE
applies it while hls.js also applies its own fragment timestampOffset, moving
the buffered range a full resume offset ahead of the playhead. The HLS muxer
must therefore be told to omit edit lists.
"""

from __future__ import annotations

import asyncio
import shutil
import struct
from pathlib import Path

import pytest

from app.services.transcode.builder import build_ffmpeg_hls_command
from app.services.transcode.decision import TranscodePlan

_CONTAINER_BOXES = {
    b"moov",
    b"trak",
    b"mdia",
    b"minf",
    b"stbl",
    b"edts",
    b"moof",
    b"traf",
    b"mvex",
}


def _box_types(data: bytes, start: int = 0, end: int | None = None):
    """Yield every ISO-BMFF box type in ``data``, descending into containers."""
    end = len(data) if end is None else end
    offset = start
    while offset + 8 <= end:
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        box_type = data[offset + 4 : offset + 8]
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[offset + 8 : offset + 16])[0]
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            break
        yield box_type
        if box_type in _CONTAINER_BOXES:
            yield from _box_types(data, offset + header, offset + size)
        offset += size


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg required to inspect generated init segments",
)
@pytest.mark.asyncio
async def test_resumed_hls_init_has_no_edit_list(tmp_path: Path) -> None:
    input_file = tmp_path / "source.mp4"
    out_dir = tmp_path / "hls_edit_list"
    out_dir.mkdir()

    # A keyframe every second lets the copy-based remux start at 2s.
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
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:sample_rate=44100",
        "-t",
        "6",
        "-c:v",
        "libx264",
        "-g",
        "24",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-shortest",
        str(input_file),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, gen_err = await gen_proc.communicate()
    assert gen_proc.returncode == 0, gen_err.decode(errors="replace")

    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        audio_stream_index=1,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        input_file,
        out_dir,
        start_offset=2.0,
        segment_duration=1,
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    assert proc.returncode == 0, err.decode(errors="replace")

    init_bytes = (out_dir / "init.mp4").read_bytes()
    boxes = set(_box_types(init_bytes))
    assert b"edts" not in boxes
    assert b"elst" not in boxes
