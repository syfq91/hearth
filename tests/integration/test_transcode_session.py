"""Integration tests for the transcode session manager and supervisor."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.core.errors import TranscodeCapacityExceededError
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import SessionManager


def _sample_plan(hw: bool = False) -> TranscodePlan:
    return TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi" if hw else "libx264",
        audio_action="transcode",
        audio_codec="aac",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="vaapi" if hw else "none",
    )


class MockProcess:
    """Mock subprocess simulating ffmpeg with stderr streaming."""

    def __init__(self, exit_code: int = 0, stderr_lines: list[bytes] | None = None) -> None:
        self.returncode: int | None = None
        self._exit_code = exit_code
        self._lines = stderr_lines or [
            b"frame=  100 fps= 45.2 q=24.0 size=N/A time=00:00:04.16 bitrate=N/A speed=1.88x\n",
            b"",
        ]
        self._idx = 0
        self.stderr = self

    async def readline(self) -> bytes:
        if self._idx < len(self._lines):
            line = self._lines[self._idx]
            self._idx += 1
            if self._idx >= len(self._lines):
                self.returncode = self._exit_code
            return line
        self.returncode = self._exit_code
        return b""

    async def wait(self) -> int:
        self.returncode = self._exit_code
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


@pytest.mark.asyncio
async def test_startup_sweep(tmp_path: Path) -> None:
    transcode_dir = tmp_path / "transcode"
    transcode_dir.mkdir()
    (transcode_dir / "session_1").mkdir()
    (transcode_dir / "session_2").mkdir()
    (transcode_dir / "session_1" / "seg_000.m4s").write_bytes(b"data")

    settings = Settings(transcode_dir=transcode_dir)
    manager = SessionManager(settings)

    cleaned = manager.startup_sweep()
    assert cleaned == 2
    assert not (transcode_dir / "session_1").exists()
    assert not (transcode_dir / "session_2").exists()


@pytest.mark.asyncio
async def test_session_lifecycle_and_progress(tmp_path: Path) -> None:
    transcode_dir = tmp_path / "transcode"
    settings = Settings(transcode_dir=transcode_dir)
    manager = SessionManager(settings)

    mock_proc = MockProcess(
        exit_code=0,
        stderr_lines=[
            b"frame=  250 fps= 59.8 size=N/A time=00:00:10.20 bitrate=N/A speed=2.12x\n",
            b"",
        ],
    )

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        session = await manager.create_session(
            user_id="user_1",
            item_id="item_1",
            file_id="file_1",
            input_path=Path("/media/movie.mp4"),
            plan=_sample_plan(hw=False),
        )

        assert session.id in manager.sessions
        # Wait a tick for stderr reader
        await asyncio.sleep(0.05)
        assert session.frame == 250
        assert session.fps == 59.8
        assert session.speed == "2.12x"

        # Stop session
        stopped = await manager.stop_session(session.id)
        assert stopped is True
        assert session.id not in manager.sessions
        assert not session.output_dir.exists()


@pytest.mark.asyncio
async def test_concurrency_cap_enforcement(tmp_path: Path) -> None:
    transcode_dir = tmp_path / "transcode"
    settings = Settings(
        transcode_dir=transcode_dir,
        transcode_max_hw_sessions=1,
        transcode_max_sw_sessions=1,
    )
    manager = SessionManager(settings)

    mock_proc = MockProcess(exit_code=0, stderr_lines=[b"running\n"])

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        # 1. HW session
        s1 = await manager.create_session(
            user_id="user_1",
            item_id="item_1",
            file_id="file_1",
            input_path=Path("/media/movie.mp4"),
            plan=_sample_plan(hw=True),
        )
        assert s1.is_hardware is True

        # Second HW session exceeds cap
        with pytest.raises(TranscodeCapacityExceededError) as exc_info:
            await manager.create_session(
                user_id="user_2",
                item_id="item_2",
                file_id="file_2",
                input_path=Path("/media/movie2.mp4"),
                plan=_sample_plan(hw=True),
            )
        assert exc_info.value.status_code == 503
        assert exc_info.value.headers.get("Retry-After") == "5"

        # SW session succeeds (separate cap)
        s_sw = await manager.create_session(
            user_id="user_3",
            item_id="item_3",
            file_id="file_3",
            input_path=Path("/media/movie3.mp4"),
            plan=_sample_plan(hw=False),
        )
        assert s_sw.is_hardware is False

        # Second SW session exceeds cap
        with pytest.raises(TranscodeCapacityExceededError) as exc_sw:
            await manager.create_session(
                user_id="user_4",
                item_id="item_4",
                file_id="file_4",
                input_path=Path("/media/movie4.mp4"),
                plan=_sample_plan(hw=False),
            )
        assert exc_sw.value.status_code == 503

        await manager.stop_all()


@pytest.mark.asyncio
async def test_automatic_hardware_to_software_fallback(tmp_path: Path) -> None:
    transcode_dir = tmp_path / "transcode"
    settings = Settings(transcode_dir=transcode_dir)
    manager = SessionManager(settings)

    failing_hw_proc = MockProcess(
        exit_code=1,
        stderr_lines=[b"libva error: vaCreateContext failed\n", b""],
    )
    successful_sw_proc = MockProcess(
        exit_code=0,
        stderr_lines=[b"frame= 50 fps= 30.0 speed=1.0x\n", b""],
    )

    call_count = 0

    async def mock_subproc(*args, **kwargs) -> MockProcess:  # noqa: ANN002, ANN003
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return failing_hw_proc
        return successful_sw_proc

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=mock_subproc)):
        session = await manager.create_session(
            user_id="user_1",
            item_id="item_1",
            file_id="file_1",
            input_path=Path("/media/movie.mp4"),
            plan=_sample_plan(hw=True),
        )

        assert session.is_hardware is True
        # Wait a tick for stderr processing and automatic fallback
        await asyncio.sleep(0.1)

        # After failure, fallback happened
        assert session.has_fallen_back is True
        assert session.is_hardware is False
        assert session.plan.hwaccel == "none"
        assert session.plan.video_codec == "libx264"
        assert call_count == 2

        await manager.stop_all()


@pytest.mark.asyncio
async def test_session_seeking(tmp_path: Path) -> None:
    transcode_dir = tmp_path / "transcode"
    settings = Settings(transcode_dir=transcode_dir)
    manager = SessionManager(settings)

    mock_proc = MockProcess(exit_code=0, stderr_lines=[b"frame= 10\n"])

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        session = await manager.create_session(
            user_id="user_1",
            item_id="item_1",
            file_id="file_1",
            input_path=Path("/media/movie.mp4"),
            plan=_sample_plan(hw=False),
            start_offset=0.0,
        )

        # Seek to 60 seconds
        await session.seek(60.0)
        assert session.start_offset == 60.0
        assert "-ss" in session.cmd
        assert session.cmd[session.cmd.index("-ss") + 1] == "60"

        await manager.stop_all()


@pytest.mark.asyncio
async def test_user_revocation_stops_sessions(tmp_path: Path) -> None:
    transcode_dir = tmp_path / "transcode"
    settings = Settings(transcode_dir=transcode_dir)
    manager = SessionManager(settings)

    mock_proc = MockProcess(exit_code=0, stderr_lines=[b"running\n"])

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
        await manager.create_session(
            user_id="user_target",
            item_id="item_1",
            file_id="file_1",
            input_path=Path("/media/movie1.mp4"),
            plan=_sample_plan(hw=False),
        )
        await manager.create_session(
            user_id="user_other",
            item_id="item_2",
            file_id="file_2",
            input_path=Path("/media/movie2.mp4"),
            plan=_sample_plan(hw=False),
        )

        assert len(manager.sessions) == 2
        stopped_count = await manager.stop_user_sessions("user_target")
        assert stopped_count == 1
        assert len(manager.sessions) == 1
        assert list(manager.sessions.values())[0].user_id == "user_other"

        await manager.stop_all()
