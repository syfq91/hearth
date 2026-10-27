"""Integration tests for admin transcoding endpoints."""

from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import TranscodeSession, get_session_manager

ADMIN = {"username": "admin", "password": "correct horse"}
USER = {"username": "member", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
async def test_admin_transcoding_capabilities_auth(api_client: httpx.AsyncClient) -> None:
    # 401 without auth
    unauth = await api_client.get("/api/v1/admin/transcoding/capabilities")
    assert unauth.status_code == 401

    admin_tokens = await _setup(api_client)

    # Normal user 403
    create_res = await api_client.post(
        "/api/v1/users",
        json={"username": USER["username"], "password": USER["password"], "role": "user"},
        headers=_bearer(admin_tokens),
    )
    assert create_res.status_code == 201
    user_login = await api_client.post("/api/v1/auth/login", json=USER)
    assert user_login.status_code == 200
    user_tokens = user_login.json()

    user_res = await api_client.get(
        "/api/v1/admin/transcoding/capabilities",
        headers=_bearer(user_tokens),
    )
    assert user_res.status_code == 403

    # Admin 200
    admin_res = await api_client.get(
        "/api/v1/admin/transcoding/capabilities",
        headers=_bearer(admin_tokens),
    )
    assert admin_res.status_code == 200
    data = admin_res.json()
    assert "render_device" in data
    assert "available" in data
    assert "software_encoders" in data
    assert "libx264" in data["software_encoders"]


@pytest.mark.asyncio
async def test_admin_transcode_sessions_management(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    admin_tokens = await _setup(api_client)

    # 1. Unauthenticated requests return 401
    assert (await api_client.get("/api/v1/admin/transcoding/sessions")).status_code == 401
    assert (
        await api_client.get("/api/v1/admin/transcoding/sessions/fake-id/logs")
    ).status_code == 401
    assert (
        await api_client.post("/api/v1/admin/transcoding/sessions/fake-id/stop")
    ).status_code == 401

    # 2. Regular user receives 403
    create_res = await api_client.post(
        "/api/v1/users",
        json={"username": USER["username"], "password": USER["password"], "role": "user"},
        headers=_bearer(admin_tokens),
    )
    assert create_res.status_code == 201
    user_login = await api_client.post("/api/v1/auth/login", json=USER)
    assert user_login.status_code == 200
    user_tokens = user_login.json()

    assert (
        await api_client.get(
            "/api/v1/admin/transcoding/sessions",
            headers=_bearer(user_tokens),
        )
    ).status_code == 403
    assert (
        await api_client.get(
            "/api/v1/admin/transcoding/sessions/fake-id/logs",
            headers=_bearer(user_tokens),
        )
    ).status_code == 403
    assert (
        await api_client.post(
            "/api/v1/admin/transcoding/sessions/fake-id/stop",
            headers=_bearer(user_tokens),
        )
    ).status_code == 403

    # 3. Initially list is empty for admin
    empty_res = await api_client.get(
        "/api/v1/admin/transcoding/sessions",
        headers=_bearer(admin_tokens),
    )
    assert empty_res.status_code == 200
    assert empty_res.json() == []

    # 4. Create an active transcode session
    manager = get_session_manager(db_settings)
    session_id = "test_admin_diag_session"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        target_width=1920,
        target_height=1080,
        target_bitrate=4_000_000,
        hwaccel="vaapi",
    )

    me_res = await api_client.get("/api/v1/auth/me", headers=_bearer(admin_tokens))
    admin_id = me_res.json()["id"]

    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_movie_1",
        file_id="file_movie_1",
        input_path=Path("/media/movie.mkv"),
        output_dir=out_dir,
        plan=plan,
        settings=db_settings,
    )
    session.fps = 48.0
    session.speed = "1.5x"
    session.frame = 250
    session.stderr_buffer.append("frame=  100 fps=45.0 speed=1.4x")
    session.stderr_buffer.append("frame=  250 fps=48.0 speed=1.5x")
    manager.sessions[session_id] = session

    # 5. List sessions as admin
    list_res = await api_client.get(
        "/api/v1/admin/transcoding/sessions",
        headers=_bearer(admin_tokens),
    )
    assert list_res.status_code == 200
    sessions_data = list_res.json()
    assert len(sessions_data) == 1
    s_info = sessions_data[0]
    assert s_info["id"] == session_id
    assert s_info["user_id"] == admin_id
    assert s_info["item_id"] == "item_movie_1"
    assert s_info["mode"] == "transcode_hls"
    assert s_info["is_hardware"] is True
    assert s_info["hwaccel"] == "vaapi"
    assert s_info["fps"] == 48.0
    assert s_info["speed"] == "1.5x"
    assert s_info["frame"] == 250
    assert s_info["uptime_seconds"] >= 0

    # 6. Tail logs
    logs_res = await api_client.get(
        f"/api/v1/admin/transcoding/sessions/{session_id}/logs",
        headers=_bearer(admin_tokens),
    )
    assert logs_res.status_code == 200
    logs_data = logs_res.json()
    assert logs_data["session_id"] == session_id
    assert len(logs_data["lines"]) == 2
    assert "speed=1.5x" in logs_data["lines"][1]

    # Non-existent session logs -> 404
    notFoundLogs = await api_client.get(
        "/api/v1/admin/transcoding/sessions/unknown_session/logs",
        headers=_bearer(admin_tokens),
    )
    assert notFoundLogs.status_code == 404

    # 7. Stop session
    stop_res = await api_client.post(
        f"/api/v1/admin/transcoding/sessions/{session_id}/stop",
        headers=_bearer(admin_tokens),
    )
    assert stop_res.status_code == 200
    assert stop_res.json() == {"status": "stopped", "session_id": session_id}

    # Second stop returns 404
    stopAgain = await api_client.post(
        f"/api/v1/admin/transcoding/sessions/{session_id}/stop",
        headers=_bearer(admin_tokens),
    )
    assert stopAgain.status_code == 404

    # 8. List is empty again
    list_after = await api_client.get(
        "/api/v1/admin/transcoding/sessions",
        headers=_bearer(admin_tokens),
    )
    assert list_after.status_code == 200
    assert list_after.json() == []
