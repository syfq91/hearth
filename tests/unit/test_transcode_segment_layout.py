"""Unit tests for the HLS segment grid, including the short opening segment."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import TranscodeSession


class _MockProcess:
    """Minimal stand-in for a running ffmpeg process."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stderr = None

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _plan(video_action: str = "transcode") -> TranscodePlan:
    return TranscodePlan(
        mode="transcode_hls" if video_action == "transcode" else "remux_hls",
        video_action=video_action,  # type: ignore[arg-type]
        video_codec="libx264" if video_action == "transcode" else "copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )


def _session(
    tmp_path: Path,
    *,
    first_segment_duration: float | None = 2.0,
    segment_duration: int = 4,
    start_offset: float = 0.0,
    video_action: str = "transcode",
) -> TranscodeSession:
    overrides: dict[str, object] = {
        "transcode_dir": tmp_path,
        "transcode_segment_duration_seconds": segment_duration,
    }
    if first_segment_duration is not None:
        overrides["transcode_first_segment_duration_seconds"] = first_segment_duration
    return TranscodeSession(
        session_id="layout_session",
        user_id="user",
        item_id="item",
        file_id="file",
        input_path=Path("/media/source.mkv"),
        output_dir=tmp_path / "out",
        plan=_plan(video_action),
        settings=Settings(**overrides),  # type: ignore[arg-type]
        start_offset=start_offset,
        duration_seconds=20.0,
    )


def test_short_first_segment_grid(tmp_path: Path) -> None:
    """A 2s opener shifts the rest of the grid by 2s, not by a whole segment."""
    session = _session(tmp_path)

    assert session.short_first_segment is True
    assert session.segment_start(0) == 0.0
    assert session.segment_start(1) == 2.0
    assert session.segment_start(2) == 6.0
    assert session.segment_start(5) == 18.0

    assert session.segment_index_at(0.0) == 0
    assert session.segment_index_at(1.9) == 0
    assert session.segment_index_at(2.0) == 1
    assert session.segment_index_at(5.9) == 1
    assert session.segment_index_at(6.0) == 2

    assert session.segment_count(20.0) == 6
    assert session.segment_count(2.0) == 1
    assert session.segment_count(2.5) == 2

    assert session.segment_duration_at(0, 20.0) == pytest.approx(2.0)
    assert session.segment_duration_at(1, 20.0) == pytest.approx(4.0)
    # The final segment is clamped to the media length (18.0 -> 20.0).
    assert session.segment_duration_at(5, 20.0) == pytest.approx(2.0)
    assert session.playlist_target_duration == 4


def test_uniform_grid_when_short_opener_disabled(tmp_path: Path) -> None:
    session = _session(tmp_path, first_segment_duration=0.0)

    assert session.short_first_segment is False
    assert session.segment_start(1) == 4.0
    assert session.segment_start(3) == 12.0
    assert session.segment_index_at(5.5) == 1
    assert session.segment_index_at(12.0) == 3
    assert session.segment_count(20.0) == 5
    assert session.playlist_target_duration == 4


def test_copy_plan_keeps_uniform_grid(tmp_path: Path) -> None:
    """Stream copies follow source keyframes, so they cannot open short."""
    session = _session(tmp_path, video_action="copy")

    assert session.short_first_segment is False
    assert session.segment_start(1) == 4.0
    assert session.segment_count(20.0) == 5


@pytest.mark.asyncio
async def test_midfile_start_uses_uniform_keyframe_grid(tmp_path: Path) -> None:
    """A resume starts on a grid boundary and must not re-anchor keyframes."""
    session = _session(tmp_path, start_offset=12.0)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_MockProcess())):
        await session.start_process()

    cmd = session.cmd
    # 12s falls in segment 3, which begins at 2 + 2 * 4 = 10s.
    assert cmd[cmd.index("-ss") + 1] == "10"
    assert cmd[cmd.index("-start_number") + 1] == "3"
    assert "-copyts" in cmd
    assert "-output_ts_offset" not in cmd
    assert cmd[cmd.index("-force_key_frames") + 1] == "expr:gte(t,10+n_forced*4)"
    assert cmd[cmd.index("-hls_time") + 1] == "4"

    await session.stop()
