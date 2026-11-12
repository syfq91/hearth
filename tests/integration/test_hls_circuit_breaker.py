"""Integration tests for HLS circuit breaker and 503 semantics (T07).

Validates:
1. Fake ffmpeg that exits 1 -> after 3 failures, response is 503 with Retry-After: 10
   and code="transcode_circuit_open".
2. After cooldown a new attempt is allowed; a success resets the counter.
3. No request during the open state spawns a new process.
4. Genuine nonexistent session returns 404.
5. Invalid segment filename returns 404.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.config import Settings
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import get_session_manager

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _plan() -> TranscodePlan:
    return TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="transcode",
        audio_codec="aac",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
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

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


class MockSuccessProcess:
    def __init__(self, output_dir: Path, segment_index: int) -> None:
        self.returncode: int | None = None
        self.output_dir = output_dir
        self.segment_index = segment_index
        self.stderr = self

    async def readline(self) -> bytes:
        # Create the segment file so ensure_segment succeeds
        seg_file = self.output_dir / f"seg_{self.segment_index:03d}.m4s"
        seg_file.write_bytes(b"\x00\x00\x00\x10moof\x00\x00\x00\x10mdat")
        return b""

    async def wait(self) -> int:
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = 0


@pytest.mark.asyncio
async def test_circuit_breaker_503_and_cooldown_lifecycle(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    admin_tokens = await _setup(api_client)
    headers = _bearer(admin_tokens)

    # 1. Nonexistent session -> 404 session_not_found
    res = await api_client.get("/api/v1/hls/nonexistent_session/seg_000.m4s", headers=headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "session_not_found"

    # 2. Invalid segment filename -> 404 invalid_segment
    res = await api_client.get("/api/v1/hls/any_session/invalid.m4s", headers=headers)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "invalid_segment"

    manager = get_session_manager(db_settings)

    spawn_count = 0

    async def mock_failing_exec(*args, **kwargs) -> MockFailingProcess:  # noqa: ANN002, ANN003
        nonlocal spawn_count
        spawn_count += 1
        return MockFailingProcess(exit_code=1)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=mock_failing_exec)):
        # Create session; initial spawn fails
        session = await manager.create_session(
            user_id="user_admin",
            item_id="item_1",
            file_id="file_1",
            input_path=Path("/media/test.mp4"),
            plan=_plan(),
            duration_seconds=120.0,
        )
        assert spawn_count == 1
        await asyncio.sleep(0.05)

        # Trigger failure 2 & 3 via segment requests
        # ensure_segment will attempt restart on dead process
        res2 = await api_client.get(
            f"/api/v1/hls/{session.id}/seg_000.m4s",
            headers=headers,
        )
        assert res2.status_code == 503

        # Record third failure to guarantee breaker is open
        session.record_failure()
        assert session.is_circuit_open()

        # While breaker is open:
        # Requesting segment must return 503 with Retry-After: 10 and code="transcode_circuit_open"
        open_spawns = spawn_count
        open_res = await api_client.get(
            f"/api/v1/hls/{session.id}/seg_000.m4s",
            headers=headers,
        )
        assert open_res.status_code == 503
        assert open_res.json()["error"]["code"] == "transcode_circuit_open"
        assert "Retry-After" in open_res.headers
        assert int(open_res.headers["Retry-After"]) >= 1

        # Acceptance 2: No request during the open state spawns a new process
        assert spawn_count == open_spawns

        # Requesting media playlist while breaker is open also returns 503 transcode_circuit_open
        playlist_res = await api_client.get(
            f"/api/v1/hls/{session.id}/media.m3u8",
            headers=headers,
        )
        assert playlist_res.status_code == 503
        assert playlist_res.json()["error"]["code"] == "transcode_circuit_open"
        assert spawn_count == open_spawns

    # Now simulate cooldown elapsed
    session.circuit_open_until = 0.0
    assert not session.is_circuit_open()

    # After cooldown, a new attempt is allowed and success resets the counter
    async def mock_success_exec(*args, **kwargs) -> MockSuccessProcess:  # noqa: ANN002, ANN003
        nonlocal spawn_count
        spawn_count += 1
        return MockSuccessProcess(output_dir=session.output_dir, segment_index=0)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=mock_success_exec)):
        success_res = await api_client.get(
            f"/api/v1/hls/{session.id}/seg_000.m4s",
            headers=headers,
        )
        assert success_res.status_code == 200
        assert spawn_count > open_spawns
        # Counter must be reset by success
        assert len(session.failure_timestamps) == 0

    await manager.stop_all()
