"""Unit tests for the transcode circuit breaker (T07).

Validates:
- 3 consecutive failures within 30 s open the breaker for 10 s.
- While open, is_circuit_open() returns True and retry_after() provides remaining cooldown.
- After cooldown, a new attempt is allowed.
- First success resets the failure counter.
- Hardware-to-software fallback restart counts as one failure.
- When breaker is open, no new ffmpeg processes are spawned.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import TranscodeSession


def _plan(hw: bool = False) -> TranscodePlan:
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


def test_circuit_breaker_opens_after_3_failures_within_30s(tmp_path: Path) -> None:
    session = TranscodeSession(
        session_id="s1",
        user_id="u1",
        item_id="i1",
        file_id="f1",
        input_path=Path("/media/test.mp4"),
        output_dir=tmp_path,
        plan=_plan(hw=False),
        settings=Settings(transcode_dir=tmp_path),
    )

    t0 = 100.0
    session.record_failure(now=t0)
    assert not session.is_circuit_open(now=t0)
    assert len(session.failure_timestamps) == 1

    t1 = 110.0
    session.record_failure(now=t1)
    assert not session.is_circuit_open(now=t1)
    assert len(session.failure_timestamps) == 2

    t2 = 120.0
    session.record_failure(now=t2)
    # 3 failures within 20 s (< 30 s window) -> breaker opens for 10 s (until 130.0)
    assert session.is_circuit_open(now=t2)
    assert session.circuit_retry_after(now=t2) == 10

    # At t=125.0: still open, 5s remaining
    assert session.is_circuit_open(now=125.0)
    assert session.circuit_retry_after(now=125.0) == 5

    # At t=130.1: cooldown elapsed -> closed
    assert not session.is_circuit_open(now=130.1)


def test_circuit_breaker_sliding_window_drops_old_failures(tmp_path: Path) -> None:
    session = TranscodeSession(
        session_id="s2",
        user_id="u1",
        item_id="i1",
        file_id="f1",
        input_path=Path("/media/test.mp4"),
        output_dir=tmp_path,
        plan=_plan(hw=False),
        settings=Settings(transcode_dir=tmp_path),
    )

    session.record_failure(now=10.0)
    session.record_failure(now=20.0)
    # 3rd failure occurs at 45.0 (35 s after first failure at 10.0)
    session.record_failure(now=45.0)

    # First failure at 10.0 has rolled out of the 30 s window [15.0..45.0]
    # Only 2 failures remain in the window, so breaker does NOT open
    assert not session.is_circuit_open(now=45.0)
    assert len(session.failure_timestamps) == 2


def test_circuit_breaker_success_resets_counter(tmp_path: Path) -> None:
    session = TranscodeSession(
        session_id="s3",
        user_id="u1",
        item_id="i1",
        file_id="f1",
        input_path=Path("/media/test.mp4"),
        output_dir=tmp_path,
        plan=_plan(hw=False),
        settings=Settings(transcode_dir=tmp_path),
    )

    session.record_failure(now=10.0)
    session.record_failure(now=15.0)
    assert len(session.failure_timestamps) == 2

    # A success occurs
    session.record_success()
    assert len(session.failure_timestamps) == 0
    assert not session.is_circuit_open(now=20.0)

    # Subsequent failure starts fresh at count 1
    session.record_failure(now=25.0)
    assert len(session.failure_timestamps) == 1
    assert not session.is_circuit_open(now=25.0)


@pytest.mark.asyncio
async def test_hardware_fallback_restart_counts_as_one_failure(tmp_path: Path) -> None:
    session = TranscodeSession(
        session_id="s4",
        user_id="u1",
        item_id="i1",
        file_id="f1",
        input_path=Path("/media/test.mp4"),
        output_dir=tmp_path,
        plan=_plan(hw=True),
        settings=Settings(transcode_dir=tmp_path),
    )

    class MockFailingProcess:
        def __init__(self, exit_code: int = 1) -> None:
            self.returncode: int | None = None
            self._exit_code = exit_code
            self.stderr = self

        async def readline(self) -> bytes:
            self.returncode = self._exit_code
            return b""

        async def wait(self) -> int:
            self.returncode = self._exit_code
            return self._exit_code

    call_count = 0

    async def mock_subproc(*args, **kwargs) -> MockFailingProcess:  # noqa: ANN002, ANN003
        nonlocal call_count
        call_count += 1
        return MockFailingProcess(exit_code=1)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=mock_subproc)):
        await session.start_process()
        # Wait a tick for stderr monitor to process exit
        await asyncio.sleep(0.05)

        # Hardware failure occurred and fallback started
        assert session.has_fallen_back is True
        # Hardware failure must be counted as 1 failure toward circuit breaker
        assert len(session.failure_timestamps) >= 1

        # Second process (software fallback) also exits with code 1
        await asyncio.sleep(0.05)
        assert len(session.failure_timestamps) == 2

        await session.stop()


@pytest.mark.asyncio
async def test_no_process_spawned_while_circuit_open(tmp_path: Path) -> None:
    session = TranscodeSession(
        session_id="s5",
        user_id="u1",
        item_id="i1",
        file_id="f1",
        input_path=Path("/media/test.mp4"),
        output_dir=tmp_path,
        plan=_plan(hw=False),
        settings=Settings(transcode_dir=tmp_path),
    )

    # Force breaker open using loop monotonic time
    loop = asyncio.get_running_loop()
    t = loop.time()
    session.record_failure(now=t)
    session.record_failure(now=t + 1)
    session.record_failure(now=t + 2)
    assert session.is_circuit_open()

    spawn_mock = AsyncMock()
    with patch("asyncio.create_subprocess_exec", new=spawn_mock):
        # 1. start_process while open must not spawn
        await session.start_process()
        assert spawn_mock.call_count == 0

        # 2. seek while open must not spawn
        await session.seek(40.0)
        assert spawn_mock.call_count == 0

        # 3. ensure_segment while open must not spawn and must return False
        ready = await session.ensure_segment(5, timeout=0.1)
        assert ready is False
        assert spawn_mock.call_count == 0
