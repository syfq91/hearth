"""Transcode session manager and process supervisor.

Manages running ffmpeg HLS fMP4 transcoding processes, concurrency caps,
progress reporting, seeking, idle reaping, cleanup, and hardware crash fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
import uuid
from collections import deque
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.errors import TranscodeCapacityExceededError
from app.db.base import utcnow
from app.services.transcode.builder import build_ffmpeg_hls_command
from app.services.transcode.decision import TranscodePlan

logger = logging.getLogger("hearth.transcode.session")

_FRAME_RE = re.compile(r"frame=\s*(\d+)")
_FPS_RE = re.compile(r"fps=\s*([\d.]+)")
_SPEED_RE = re.compile(r"speed=\s*([\d.]+)x")


class TranscodeSession:
    """A supervised ffmpeg transcoding process outputting HLS fMP4 segments."""

    def __init__(
        self,
        session_id: str,
        user_id: str,
        item_id: str,
        file_id: str,
        input_path: Path,
        output_dir: Path,
        plan: TranscodePlan,
        settings: Settings,
        start_offset: float = 0.0,
    ) -> None:
        self.id = session_id
        self.user_id = user_id
        self.item_id = item_id
        self.file_id = file_id
        self.input_path = input_path
        self.output_dir = output_dir
        self.plan = plan
        self.settings = settings
        self.start_offset = start_offset

        self.created_at = utcnow()
        self.last_activity_at = utcnow()
        self.is_hardware = plan.hwaccel == "vaapi"
        self.is_stopped = False
        self.has_fallen_back = False

        self.process: asyncio.subprocess.Process | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self.stderr_buffer: deque[str] = deque(maxlen=200)

        self.frame: int | None = None
        self.fps: float | None = None
        self.speed: str | None = None
        self.cmd: list[str] = []

    def touch(self) -> None:
        """Mark recent client activity."""
        self.last_activity_at = utcnow()

    @property
    def uptime_seconds(self) -> float:
        """Duration since session creation."""
        return (utcnow() - self.created_at).total_seconds()

    async def start_process(self) -> None:
        """Build command and launch ffmpeg subprocess."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.cmd = build_ffmpeg_hls_command(
            self.plan,
            self.input_path,
            self.output_dir,
            ffmpeg_binary=self.settings.ffmpeg_binary,
            render_device=str(self.settings.render_device),
            start_offset=self.start_offset,
            segment_duration=self.settings.transcode_segment_duration_seconds,
            audio_bitrate_kbps=self.settings.transcode_audio_bitrate_kbps,
        )

        logger.info(
            "starting transcode session %s (mode=%s, hw=%s)",
            self.id,
            self.plan.mode,
            self.is_hardware,
            extra={"session_id": self.id, "cmd": self.cmd},
        )

        self.process = await asyncio.create_subprocess_exec(
            *self.cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._monitor_task = asyncio.create_task(self._monitor_stderr())

    async def _monitor_stderr(self) -> None:
        """Read and parse ffmpeg stderr for progress and crash detection."""
        if self.process is None or self.process.stderr is None:
            return

        try:
            while True:
                line_bytes = await self.process.stderr.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode(errors="replace").strip()
                if not line:
                    continue
                self.stderr_buffer.append(line)

                # Parse progress stats
                f_match = _FRAME_RE.search(line)
                if f_match:
                    self.frame = int(f_match.group(1))
                fps_match = _FPS_RE.search(line)
                if fps_match:
                    self.fps = float(fps_match.group(1))
                speed_match = _SPEED_RE.search(line)
                if speed_match:
                    self.speed = f"{speed_match.group(1)}x"

            await self.process.wait()
            returncode = self.process.returncode

            if returncode != 0 and not self.is_stopped:
                logger.warning(
                    "transcode session %s exited with code %s",
                    self.id,
                    returncode,
                    extra={"session_id": self.id, "returncode": returncode},
                )
                # Crash detection and fallback from hardware to software
                if self.is_hardware and not self.has_fallen_back:
                    logger.info("attempting software fallback for session %s", self.id)
                    self.has_fallen_back = True
                    self.is_hardware = False
                    self.plan.hwaccel = "none"
                    self.plan.video_codec = "libx264"
                    await self.start_process()

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("error monitoring transcode session %s", self.id)

    async def wait_for_first_segment(self, timeout: float = 15.0) -> bool:
        """Wait until media.m3u8 and init.mp4 exist on disk."""
        m3u8 = self.output_dir / "media.m3u8"
        init = self.output_dir / "init.mp4"
        start = asyncio.get_running_loop().time()

        while asyncio.get_running_loop().time() - start < timeout:
            if m3u8.exists() and init.exists():
                return True
            if (
                self.process
                and self.process.returncode is not None
                and not self.is_hardware
                and self.has_fallen_back
            ):
                # Died even after fallback
                return False
            await asyncio.sleep(0.1)

        return m3u8.exists() and init.exists()

    async def seek(self, offset_seconds: float) -> None:
        """Seek to a new offset by restarting ffmpeg with new -ss."""
        logger.info("seeking session %s to %gs", self.id, offset_seconds)
        self.start_offset = offset_seconds
        await self._stop_process()
        # Clean existing segments
        if self.output_dir.exists():
            for p in self.output_dir.iterdir():
                if p.is_file():
                    p.unlink(missing_ok=True)
        await self.start_process()

    async def _stop_process(self) -> None:
        """Terminate the ffmpeg process cleanly."""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task

        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=3.0)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            except ProcessLookupError:
                pass
            self.process = None

    async def stop(self) -> None:
        """Stop the session and remove output files."""
        self.is_stopped = True
        await self._stop_process()
        if self.output_dir.exists():
            try:
                shutil.rmtree(self.output_dir, ignore_errors=True)
            except Exception:
                logger.warning("failed removing output directory %s", self.output_dir)


class SessionManager:
    """Supervisor for active transcoding sessions."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.sessions: dict[str, TranscodeSession] = {}
        self._lock = asyncio.Lock()
        self._reaper_task: asyncio.Task[None] | None = None

    async def create_session(
        self,
        user_id: str,
        item_id: str,
        file_id: str,
        input_path: Path,
        plan: TranscodePlan,
        start_offset: float = 0.0,
    ) -> TranscodeSession:
        """Create and start a new transcoding session, respecting concurrency limits."""
        async with self._lock:
            # Check concurrency caps
            is_hw = plan.hwaccel == "vaapi"
            active_hw = sum(1 for s in self.sessions.values() if s.is_hardware and not s.is_stopped)
            active_sw = sum(
                1 for s in self.sessions.values() if not s.is_hardware and not s.is_stopped
            )

            if is_hw and active_hw >= self.settings.transcode_max_hw_sessions:
                raise TranscodeCapacityExceededError(
                    message="Hardware transcoding concurrency limit reached",
                    retry_after=5,
                )
            if not is_hw and active_sw >= self.settings.transcode_max_sw_sessions:
                raise TranscodeCapacityExceededError(
                    message="Software transcoding concurrency limit reached",
                    retry_after=5,
                )

            session_id = uuid.uuid4().hex
            output_dir = self.settings.transcode_dir / session_id

            session = TranscodeSession(
                session_id=session_id,
                user_id=user_id,
                item_id=item_id,
                file_id=file_id,
                input_path=input_path,
                output_dir=output_dir,
                plan=plan,
                settings=self.settings,
                start_offset=start_offset,
            )
            self.sessions[session_id] = session
            await session.start_process()
            self._ensure_reaper_running()
            return session

    def get_session(self, session_id: str) -> TranscodeSession | None:
        """Return active session if found."""
        session = self.sessions.get(session_id)
        if session and not session.is_stopped:
            return session
        return None

    async def stop_session(self, session_id: str) -> bool:
        """Stop and remove a session."""
        async with self._lock:
            session = self.sessions.pop(session_id, None)
            if session:
                await session.stop()
                return True
            return False

    async def stop_user_sessions(self, user_id: str) -> int:
        """Stop all sessions belonging to a specific user (e.g. on revocation)."""
        async with self._lock:
            to_stop = [s for s in self.sessions.values() if s.user_id == user_id]
            for s in to_stop:
                self.sessions.pop(s.id, None)
                await s.stop()
            return len(to_stop)

    async def stop_all(self) -> None:
        """Stop all sessions cleanly (e.g. at shutdown)."""
        async with self._lock:
            sessions = list(self.sessions.values())
            self.sessions.clear()
            for s in sessions:
                await s.stop()
            if self._reaper_task and not self._reaper_task.done():
                self._reaper_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._reaper_task

    def list_sessions(self) -> list[TranscodeSession]:
        """Return list of current sessions."""
        return [s for s in self.sessions.values() if not s.is_stopped]

    def startup_sweep(self) -> int:
        """Clean any stale session directories in transcode_dir."""
        transcode_dir = self.settings.transcode_dir
        if not transcode_dir.exists():
            return 0
        cleaned = 0
        for entry in transcode_dir.iterdir():
            if entry.is_dir():
                try:
                    shutil.rmtree(entry, ignore_errors=True)
                    cleaned += 1
                except Exception:
                    pass
        return cleaned

    def _ensure_reaper_running(self) -> None:
        if self._reaper_task is None or self._reaper_task.done():
            self._reaper_task = asyncio.create_task(self._reap_idle_loop())

    async def _reap_idle_loop(self) -> None:
        """Periodically reap sessions exceeding the idle timeout."""
        try:
            while True:
                await asyncio.sleep(5.0)
                now = utcnow()
                idle_timeout = self.settings.transcode_idle_timeout_seconds

                async with self._lock:
                    stale_ids = [
                        s.id
                        for s in self.sessions.values()
                        if (now - s.last_activity_at).total_seconds() > idle_timeout
                    ]
                    for sid in stale_ids:
                        session = self.sessions.pop(sid, None)
                        if session:
                            logger.info("reaping idle transcode session %s", sid)
                            await session.stop()

                if not self.sessions:
                    break
        except asyncio.CancelledError:
            pass


_session_manager: SessionManager | None = None


def get_session_manager(settings: Settings | None = None) -> SessionManager:
    """Return the global session manager singleton."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager(settings)
    return _session_manager


__all__ = [
    "SessionManager",
    "TranscodeSession",
    "get_session_manager",
]
