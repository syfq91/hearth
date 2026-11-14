"""Unit tests for T06: Transcode Throttling (§3.5.1).

Validates:
- Pausing via SIGSTOP when lead >= 60s.
- Resuming via SIGCONT when lead <= 20s.
- Idempotent ticks without redundant signal deliveries.
- Hysteresis band between 20s and 60s.
- Resume before termination on stop() per INV-6.
- Idle reaper cleanup of paused sessions.
"""

from __future__ import annotations

import signal
from datetime import timedelta
from pathlib import Path

import pytest

from app.core.config import Settings
from app.db.base import utcnow
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import SessionManager, TranscodeSession


class MockSubprocess:
    """Mock asyncio.subprocess.Process recording signals and termination."""

    def __init__(self, pid: int = 12345) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.signals_received: list[signal.Signals] = []
        self.call_order: list[str] = []

    def send_signal(self, sig: int) -> None:
        self.signals_received.append(signal.Signals(sig))
        if sig == signal.SIGSTOP:
            self.call_order.append("SIGSTOP")
        elif sig == signal.SIGCONT:
            self.call_order.append("SIGCONT")

    def terminate(self) -> None:
        self.call_order.append("terminate")
        self.returncode = 0

    def kill(self) -> None:
        self.call_order.append("kill")
        self.returncode = -9

    async def wait(self) -> int:
        return 0


def _create_test_session(
    tmp_path: Path,
    session_id: str = "test_throttle_session",
    segment_duration: int = 4,
) -> tuple[TranscodeSession, MockSubprocess]:
    out_dir = tmp_path / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "init.mp4").write_bytes(b"init")
    (out_dir / "media.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

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
    settings = Settings(
        transcode_dir=tmp_path,
        transcode_segment_duration_seconds=segment_duration,
        transcode_idle_timeout_seconds=60.0,
    )
    session = TranscodeSession(
        session_id=session_id,
        user_id="u1",
        item_id="i1",
        file_id="f1",
        input_path=tmp_path / "movie.mp4",
        output_dir=out_dir,
        plan=plan,
        settings=settings,
        duration_seconds=7200.0,
    )
    proc = MockSubprocess()
    session.process = proc  # type: ignore[assignment]
    return session, proc


def _create_segments(session: TranscodeSession, count: int) -> None:
    for i in range(count):
        (session.output_dir / f"seg_{i:03d}.m4s").write_bytes(b"seg")


@pytest.mark.asyncio
async def test_throttler_pause_at_lead_60_and_resume_at_lead_20(tmp_path: Path) -> None:
    """Lead >= 60s pauses process via SIGSTOP; lead <= 20s resumes via SIGCONT."""
    session, proc = _create_test_session(tmp_path)
    manager = SessionManager(session.settings)
    manager.sessions[session.id] = session

    # 1. 16 segments of 4s = 64s encoded time. With 0s served, lead = 64s (>= 60s)
    _create_segments(session, 16)
    assert session.encoded_time_seconds == 64.0
    assert session.compute_lead() == 64.0
    assert not session.is_paused

    await manager.throttle_sessions()

    assert session.is_paused is True
    assert proc.signals_received == [signal.SIGSTOP]

    # 2. Repeated tick with lead >= 60s should be idempotent (no extra SIGSTOP)
    await manager.throttle_sessions()
    assert len(proc.signals_received) == 1

    # 3. Client requests segment 11 (served end = 12 * 4 = 48s).
    # Lead becomes 64s - 48s = 16s (<= 20s).
    session.record_served_segment(11)

    assert session.last_served_segment_end == 48.0
    assert session.compute_lead() == 16.0
    assert session.is_paused is False
    assert proc.signals_received == [signal.SIGSTOP, signal.SIGCONT]

    # 4. Repeated tick with lead <= 20s should be idempotent (no extra SIGCONT)
    await manager.throttle_sessions()
    assert len(proc.signals_received) == 2


@pytest.mark.asyncio
async def test_throttler_hysteresis_band(tmp_path: Path) -> None:
    """Between 20s and 60s lead, state does not toggle."""
    session, proc = _create_test_session(tmp_path)
    manager = SessionManager(session.settings)
    manager.sessions[session.id] = session

    # 10 segments of 4s = 40s encoded. Lead = 40s.
    _create_segments(session, 10)
    assert session.compute_lead() == 40.0

    # Running session stays running
    await manager.throttle_sessions()
    assert not session.is_paused
    assert len(proc.signals_received) == 0

    # If already paused, a lead of 40s stays paused
    session.is_paused = True
    await manager.throttle_sessions()
    assert session.is_paused is True
    assert len(proc.signals_received) == 0


@pytest.mark.asyncio
async def test_stop_on_paused_session_resumes_before_terminating(tmp_path: Path) -> None:
    """INV-6: A paused process is always resumed (SIGCONT) before terminate() on stop."""
    session, proc = _create_test_session(tmp_path)
    session.is_paused = True

    await session.stop()

    assert session.is_stopped is True
    assert session.is_paused is False
    assert proc.call_order == ["SIGCONT", "terminate"]


@pytest.mark.asyncio
async def test_idle_reaper_cleans_up_paused_session(tmp_path: Path) -> None:
    """A paused session is reaped cleanly after idle timeout without leaving a stopped process."""
    session, proc = _create_test_session(tmp_path)
    session.is_paused = True
    manager = SessionManager(session.settings)
    manager.sessions[session.id] = session

    # Set last activity beyond idle timeout (60s)
    session.last_activity_at = utcnow() - timedelta(seconds=70)

    reaped = await manager.reap_idle_sessions()

    assert session.id in reaped
    assert session.id not in manager.sessions
    assert session.is_stopped is True
    assert proc.call_order == ["SIGCONT", "terminate"]
