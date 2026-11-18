"""Audio bitrate budgeting for HLS transcode sessions."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.services.transcode.decision import TranscodePlan, budget_audio_bitrate
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


def _session(tmp_path: Path, target_bitrate: int | None) -> TranscodeSession:
    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="transcode",
        audio_codec="aac",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
        target_bitrate=target_bitrate,
    )
    return TranscodeSession(
        session_id="audio-bitrate",
        user_id="user",
        item_id="item",
        file_id="file",
        input_path=Path("/media/source.mkv"),
        output_dir=tmp_path / "out",
        plan=plan,
        settings=Settings(transcode_dir=tmp_path),
    )


async def _argv_for(tmp_path: Path, target_bitrate: int | None) -> list[str]:
    session = _session(tmp_path, target_bitrate)
    exec_mock = AsyncMock(return_value=_MockProcess())
    with patch("asyncio.create_subprocess_exec", new=exec_mock):
        await session.start_process()
    await session.stop()
    assert exec_mock.await_args is not None
    return list(exec_mock.await_args.args)


def _audio_bitrate(argv: list[str]) -> str:
    return argv[argv.index("-b:a") + 1]


def test_budget_audio_bitrate_thresholds() -> None:
    assert budget_audio_bitrate(640_000) == 128_000
    assert budget_audio_bitrate(2_000_000) == 192_000
    assert budget_audio_bitrate(10_000_000) == 384_000
    assert budget_audio_bitrate(20_000_000) == 640_000


@pytest.mark.asyncio
async def test_no_cap_uses_admin_setting(tmp_path: Path) -> None:
    assert _audio_bitrate(await _argv_for(tmp_path, None)) == "192k"


@pytest.mark.asyncio
async def test_tight_cap_lowers_audio_share(tmp_path: Path) -> None:
    assert _audio_bitrate(await _argv_for(tmp_path, 500_000)) == "128k"


@pytest.mark.asyncio
async def test_generous_cap_does_not_raise_admin_ceiling(tmp_path: Path) -> None:
    assert _audio_bitrate(await _argv_for(tmp_path, 8_000_000)) == "192k"
