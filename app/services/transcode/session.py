"""Transcode session manager and process supervisor.

Manages running ffmpeg HLS fMP4 transcoding processes, concurrency caps,
progress reporting, seeking, idle reaping, cleanup, and hardware crash fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import re
import shutil
import uuid
from collections import deque
from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.errors import TranscodeCapacityExceededError
from app.db.base import utcnow
from app.services.media.probe import ProbeStream
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
        duration_seconds: float = 0.0,
        source_video: ProbeStream | None = None,
        source_audio: ProbeStream | None = None,
        source_fps: float | None = None,
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
        self.duration_seconds = duration_seconds
        self.source_video = source_video
        self.source_audio = source_audio
        self.source_fps = source_fps
        self.segment_duration = settings.transcode_segment_duration_seconds
        self._seek_lock = asyncio.Lock()
        self.current_start_segment = (
            int(start_offset // self.segment_duration) if self.segment_duration > 0 else 0
        )

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

        # Circuit breaker state (§3.5.1, T07)
        self.failure_timestamps: list[float] = []
        self.circuit_open_until: float = 0.0
        self.circuit_failures_threshold: int = 3
        self.circuit_window_seconds: float = 30.0
        self.circuit_cooldown_seconds: float = 10.0

    def record_failure(self, now: float | None = None) -> None:
        """Record an ffmpeg startup or exit failure and evaluate circuit breaker threshold."""
        try:
            loop_time = now if now is not None else asyncio.get_running_loop().time()
        except RuntimeError:
            import time

            loop_time = now if now is not None else time.monotonic()

        cutoff = loop_time - self.circuit_window_seconds
        self.failure_timestamps = [t for t in self.failure_timestamps if t >= cutoff]
        self.failure_timestamps.append(loop_time)

        if len(self.failure_timestamps) >= self.circuit_failures_threshold:
            self.circuit_open_until = loop_time + self.circuit_cooldown_seconds
            logger.warning(
                "transcode circuit breaker OPEN for session %s (%d fails in %.1fs; open for %.1fs)",
                self.id,
                len(self.failure_timestamps),
                self.circuit_window_seconds,
                self.circuit_cooldown_seconds,
                extra={
                    "session_id": self.id,
                    "failures": len(self.failure_timestamps),
                    "open_until": self.circuit_open_until,
                },
            )

    def record_success(self) -> None:
        """Reset consecutive failures upon successful production/serving of a segment."""
        self.failure_timestamps.clear()
        self.circuit_open_until = 0.0

    def is_circuit_open(self, now: float | None = None) -> bool:
        """Return True if circuit breaker is currently open."""
        try:
            loop_time = now if now is not None else asyncio.get_running_loop().time()
        except RuntimeError:
            import time

            loop_time = now if now is not None else time.monotonic()
        return loop_time < self.circuit_open_until

    def circuit_retry_after(self, now: float | None = None) -> int:
        """Return integer seconds for Retry-After header while circuit breaker is open."""
        try:
            loop_time = now if now is not None else asyncio.get_running_loop().time()
        except RuntimeError:
            import time

            loop_time = now if now is not None else time.monotonic()
        remaining = self.circuit_open_until - loop_time
        return max(1, int(math.ceil(remaining)))

    def touch(self) -> None:
        """Mark recent client activity."""
        self.last_activity_at = utcnow()

    @property
    def uptime_seconds(self) -> float:
        """Duration since session creation."""
        return (utcnow() - self.created_at).total_seconds()

    async def start_process(self) -> None:
        """Build command and launch ffmpeg subprocess."""
        if self.is_circuit_open():
            logger.warning(
                "refusing to start ffmpeg process for session %s: circuit breaker is open",
                self.id,
                extra={"session_id": self.id},
            )
            return

        self.output_dir.mkdir(parents=True, exist_ok=True)
        start_seg = (
            int(self.start_offset // self.segment_duration) if self.segment_duration > 0 else 0
        )
        self.current_start_segment = start_seg
        cmd_offset = start_seg * self.segment_duration
        self.cmd = build_ffmpeg_hls_command(
            self.plan,
            self.input_path,
            self.output_dir,
            ffmpeg_binary=self.settings.ffmpeg_binary,
            render_device=str(self.settings.render_device),
            start_offset=cmd_offset,
            start_number=start_seg,
            segment_duration=self.segment_duration,
            audio_bitrate_kbps=self.settings.transcode_audio_bitrate_kbps,
        )

        logger.info(
            "starting transcode session %s (mode=%s, hw=%s)",
            self.id,
            self.plan.mode,
            self.is_hardware,
            extra={"session_id": self.id, "cmd": self.cmd},
        )

        def _set_nice() -> None:
            import os

            with contextlib.suppress(Exception):
                os.nice(10)

        try:
            self.process = await asyncio.create_subprocess_exec(
                *self.cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=_set_nice,
            )
            self._monitor_task = asyncio.create_task(self._monitor_stderr())
        except Exception:
            logger.exception("failed to spawn ffmpeg for session %s", self.id)
            self.record_failure()

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
                tail = "".join(list(self.stderr_buffer)[-10:])
                logger.warning(
                    "transcode session %s exited with code %s; stderr:\n%s",
                    self.id,
                    returncode,
                    tail,
                    extra={"session_id": self.id, "returncode": returncode, "stderr": tail},
                )
                self.record_failure()
                # Crash detection and fallback from hardware to software
                if self.is_hardware and not self.has_fallen_back:
                    logger.info("attempting software fallback for session %s", self.id)
                    self.has_fallen_back = True
                    self.is_hardware = False
                    self.plan.hwaccel = "none"
                    self.plan.video_codec = "libx264"
                    if not self.is_circuit_open():
                        await self.start_process()

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("error monitoring transcode session %s", self.id)

    async def wait_for_first_segment(self, timeout: float = 15.0) -> bool:
        """Wait until media.m3u8 and init.mp4 exist on disk."""
        m3u8 = self.output_dir / "media.m3u8"
        init = self.output_dir / "init.mp4"
        if m3u8.exists() and init.exists():
            self.record_success()
            return True

        if self.is_circuit_open():
            return False

        start = asyncio.get_running_loop().time()

        while asyncio.get_running_loop().time() - start < timeout:
            if m3u8.exists() and init.exists():
                self.record_success()
                return True
            if self.is_circuit_open():
                return False
            if (
                self.process
                and self.process.returncode is not None
                and (not self.is_hardware or self.has_fallen_back)
            ):
                # Died even after fallback (or died during software transcode)
                return False
            await asyncio.sleep(0.1)

        return m3u8.exists() and init.exists()

    async def ensure_segment(self, segment_index: int, timeout: float = 30.0) -> bool:
        """Ensure that seg_{segment_index:03d}.m4s exists on disk, seeking if necessary."""
        seg_file = self.output_dir / f"seg_{segment_index:03d}.m4s"
        if seg_file.exists() and seg_file.stat().st_size > 0:
            self.record_success()
            return True

        if self.is_circuit_open():
            return False

        async with self._seek_lock:
            if seg_file.exists() and seg_file.stat().st_size > 0:
                self.record_success()
                return True

            if self.is_circuit_open():
                return False

            latest_seg = -1
            for p in self.output_dir.glob("seg_*.m4s"):
                with contextlib.suppress(ValueError, IndexError):
                    idx = int(p.stem.split("_")[1])
                    if idx > latest_seg:
                        latest_seg = idx

            is_running = self.process is not None and self.process.returncode is None
            near_active = (
                is_running
                and self.current_start_segment <= segment_index
                and (latest_seg == -1 or segment_index <= latest_seg + 3)
            )

            if not near_active:
                seek_offset = segment_index * self.segment_duration
                logger.info(
                    "seeking transcode session %s to segment %d (offset %.1fs)",
                    self.id,
                    segment_index,
                    seek_offset,
                )
                self.start_offset = float(seek_offset)
                await self._stop_process()
                await self.start_process()

            start = asyncio.get_running_loop().time()
            while asyncio.get_running_loop().time() - start < timeout:
                if seg_file.exists() and seg_file.stat().st_size > 0:
                    self.record_success()
                    return True
                if self.is_circuit_open():
                    return False
                if (
                    self.process
                    and self.process.returncode is not None
                    and (not self.is_hardware or self.has_fallen_back)
                ):
                    return False
                await asyncio.sleep(0.05)

            return seg_file.exists() and seg_file.stat().st_size > 0

    async def seek(self, offset_seconds: float) -> None:
        """Seek to a new offset non-destructively (INV-8).

        If target segment already exists on disk, do nothing (reuse cached segment).
        Otherwise terminate active ffmpeg (respecting INV-6) without touching the output
        directory, then spawn a new ffmpeg starting at target_segment.
        """
        if self.is_circuit_open():
            logger.warning(
                "refusing seek for session %s: circuit breaker is open",
                self.id,
                extra={"session_id": self.id},
            )
            return

        target_seg = (
            int(offset_seconds // self.segment_duration) if self.segment_duration > 0 else 0
        )
        target_file = self.output_dir / f"seg_{target_seg:03d}.m4s"

        if target_file.exists() and target_file.stat().st_size > 0:
            logger.info(
                "seek session %s to %gs: segment %d already cached on disk; skipping restart",
                self.id,
                offset_seconds,
                target_seg,
            )
            return

        async with self._seek_lock:
            # Recheck after acquiring lock
            if target_file.exists() and target_file.stat().st_size > 0:
                logger.info(
                    "seek session %s to %gs: segment %d cached after lock; skipping restart",
                    self.id,
                    offset_seconds,
                    target_seg,
                )
                return

            seek_offset = float(target_seg * self.segment_duration)
            logger.info(
                "seeking session %s to %gs (aligned segment %d, offset %.1fs)",
                self.id,
                offset_seconds,
                target_seg,
                seek_offset,
            )
            self.start_offset = seek_offset
            await self._stop_process()
            # Non-destructive: existing segment files are preserved (INV-8)
            await self.start_process()

    async def _stop_process(self) -> None:
        """Terminate the ffmpeg process cleanly (respecting INV-6)."""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task

        if self.process and self.process.returncode is None:
            # A paused ffmpeg process is always resumed before it is terminated (INV-6)
            if getattr(self, "is_paused", False):
                with contextlib.suppress(Exception):
                    import signal

                    self.process.send_signal(signal.SIGCONT)
                    self.is_paused = False

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
        duration_seconds: float = 0.0,
        source_video: ProbeStream | None = None,
        source_audio: ProbeStream | None = None,
        source_fps: float | None = None,
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

            # Check disk space for cache/transcode directory
            try:
                self.settings.transcode_dir.mkdir(parents=True, exist_ok=True)
                free_bytes = shutil.disk_usage(self.settings.transcode_dir).free
                if free_bytes < 50 * 1024 * 1024:
                    raise TranscodeCapacityExceededError(
                        message="Transcode cache disk full",
                        retry_after=30,
                    )
            except TranscodeCapacityExceededError:
                raise
            except OSError:
                pass

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
                duration_seconds=duration_seconds,
                source_video=source_video,
                source_audio=source_audio,
                source_fps=source_fps,
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
    if _session_manager is None or (
        settings is not None and _session_manager.settings is not settings
    ):
        _session_manager = SessionManager(settings)
    return _session_manager


__all__ = [
    "SessionManager",
    "TranscodeSession",
    "get_session_manager",
]
