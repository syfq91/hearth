"""Integration tests for HLS fMP4 delivery endpoints."""

from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from app.core.config import Settings
from app.core.secrets import load_keys
from app.core.tokens import create_media_token
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import TranscodeSession, get_session_manager

ADMIN = {"username": "admin", "password": "correct horse"}
USER = {"username": "user1", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
async def test_hls_endpoints_full_cycle(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    tmp_path: Path,
) -> None:
    admin_tokens = await _setup(api_client)

    # 1. Unauthenticated request returns 401
    res = await api_client.get("/api/v1/hls/fake_session/master.m3u8")
    assert res.status_code == 401

    # 2. Unknown session returns 404
    res = await api_client.get(
        "/api/v1/hls/fake_session/master.m3u8",
        headers=_bearer(admin_tokens),
    )
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "session_not_found"

    # 3. Create a simulated active session in session manager
    manager = get_session_manager(db_settings)
    session_id = "test_hls_session_123"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write sample files
    (out_dir / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypiso5")
    (out_dir / "seg_000.m4s").write_bytes(b"\x00\x00\x00\x10moof")
    media_m3u8_content = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:7\n"
        "#EXT-X-TARGETDURATION:4\n"
        "#EXT-X-MEDIA-SEQUENCE:0\n"
        '#EXT-X-MAP:URI="init.mp4"\n'
        "#EXTINF:4.000,\n"
        "seg_000.m4s\n"
    )
    (out_dir / "media.m3u8").write_text(media_m3u8_content, encoding="utf-8")

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
        target_bitrate=5_000_000,
        hwaccel="vaapi",
    )

    # Create dummy admin user id from me
    me_res = await api_client.get("/api/v1/auth/me", headers=_bearer(admin_tokens))
    admin_id = me_res.json()["id"]

    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_dummy",
        file_id="file_dummy",
        input_path=Path("/media/test.mp4"),
        output_dir=out_dir,
        plan=plan,
        settings=db_settings,
    )
    manager.sessions[session_id] = session

    # 4. Generate media access token
    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=admin_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="item_dummy",
    )
    encoded_token = quote(token, safe="")

    # 5. Fetch master playlist via media token query param
    master_res = await api_client.get(f"/api/v1/hls/{session_id}/master.m3u8?token={encoded_token}")
    assert master_res.status_code == 200
    assert master_res.headers["Content-Type"].startswith("application/vnd.apple.mpegurl")
    master_text = master_res.text
    assert "#EXT-X-STREAM-INF" in master_text
    assert f"media.m3u8?token={encoded_token}" in master_text

    # 6. Fetch media playlist via media token
    media_res = await api_client.get(f"/api/v1/hls/{session_id}/media.m3u8?token={encoded_token}")
    assert media_res.status_code == 200
    media_text = media_res.text
    assert f'URI="init.mp4?token={encoded_token}"' in media_text
    assert f"seg_000.m4s?token={encoded_token}" in media_text

    # 7. Fetch init segment
    init_res = await api_client.get(f"/api/v1/hls/{session_id}/init.mp4?token={encoded_token}")
    assert init_res.status_code == 200
    assert init_res.headers["Content-Type"] == "video/mp4"
    assert init_res.content == b"\x00\x00\x00\x20ftypiso5"

    # 8. Fetch media segment
    seg_res = await api_client.get(f"/api/v1/hls/{session_id}/seg_000.m4s?token={encoded_token}")
    assert seg_res.status_code == 200
    assert seg_res.content == b"\x00\x00\x00\x10moof"

    # 9. Invalid segment filename rejected
    invalid_seg = await api_client.get(
        f"/api/v1/hls/{session_id}/malicious.txt?token={encoded_token}"
    )
    assert invalid_seg.status_code == 404
    assert invalid_seg.json()["error"]["code"] == "invalid_segment"

    # 10. Stop session
    stop_res = await api_client.post(
        f"/api/v1/hls/{session_id}/stop",
        headers=_bearer(admin_tokens),
    )
    assert stop_res.status_code == 200
    assert stop_res.json()["stopped"] is True
    assert session_id not in manager.sessions
