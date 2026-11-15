"""Unit tests for transcode process scheduling priority."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import TranscodeSession


class _MockProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.stderr = None

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        return self.returncode or 0


def _session(tmp_path: Path, hwaccel: str, codec: str) -> TranscodeSession:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec=codec,
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel=hwaccel,  # type: ignore[arg-type]
    )
    return TranscodeSession(
        session_id=f"priority_{hwaccel}",
        user_id="user",
        item_id="item",
        file_id="file",
        input_path=Path("/media/source.mkv"),
        output_dir=tmp_path / f"out_{hwaccel}",
        plan=plan,
        settings=Settings(transcode_dir=tmp_path),
        duration_seconds=600.0,
    )


@pytest.mark.asyncio
async def test_hardware_session_keeps_default_priority(tmp_path: Path) -> None:
    """VA-API work happens on the GPU; do not nice the process below the API."""
    session = _session(tmp_path, "vaapi", "h264_vaapi")

    exec_mock = AsyncMock(return_value=_MockProcess())
    with patch("asyncio.create_subprocess_exec", new=exec_mock):
        await session.start_process()

    assert exec_mock.await_args is not None
    assert exec_mock.await_args.kwargs["preexec_fn"] is None
    await session.stop()


@pytest.mark.asyncio
async def test_software_session_is_niced(tmp_path: Path) -> None:
    """Software encoding competes for CPU with the API and worker, so it yields."""
    session = _session(tmp_path, "none", "libx264")

    exec_mock = AsyncMock(return_value=_MockProcess())
    with patch("asyncio.create_subprocess_exec", new=exec_mock):
        await session.start_process()

    assert exec_mock.await_args is not None
    preexec_fn = exec_mock.await_args.kwargs["preexec_fn"]
    assert callable(preexec_fn)
    await session.stop()
