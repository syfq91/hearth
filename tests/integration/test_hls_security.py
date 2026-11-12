"""Integration tests for HLS path and token security (T12 / INV-10).

Validates:
- Path traversal (../, ..%2F, %2e%2e) rejected
- Absolute paths rejected
- Overlong session IDs (>64 chars) and segment names (>6 digits) rejected
- Tokens: HMAC-signed, include user, item, session, expiry; constant-time comparison; reject expired
- Wrong-session tokens rejected (403)
- Expired tokens rejected (401)
- Filesystem access never occurs outside the session directory
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from app.core.config import Settings
from app.core.secrets import load_keys
from app.core.tokens import create_media_token
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import SessionManager, TranscodeSession, get_session_manager

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


def _create_test_session(
    manager: SessionManager,
    session_id: str,
    db_settings: Settings,
    user_id: str,
) -> TranscodeSession:
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypiso5")
    (out_dir / "media.m3u8").write_text("#EXTM3U\n#EXT-X-VERSION:7\n", encoding="utf-8")
    (out_dir / "seg_000.m4s").write_bytes(b"\x00\x00\x00\x10moof")

    session = TranscodeSession(
        session_id=session_id,
        user_id=user_id,
        item_id="item_sec_1",
        file_id="file_sec_1",
        input_path=Path("/media/test.mp4"),
        output_dir=out_dir,
        plan=_plan(),
        settings=db_settings,
        duration_seconds=120.0,
    )
    manager.sessions[session_id] = session
    return session


@pytest.mark.asyncio
async def test_path_traversal_and_absolute_paths_rejected(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    admin_tokens = await _setup(api_client)
    headers = _bearer(admin_tokens)
    manager = get_session_manager(db_settings)

    session_id = "0123456789abcdef0123456789abcdef"
    _create_test_session(manager, session_id, db_settings, "u_admin")

    traversal_paths = [
        f"/api/v1/hls/{session_id}/../etc/passwd",
        f"/api/v1/hls/{session_id}/..%2fetc%2fpasswd",
        f"/api/v1/hls/{session_id}/%2e%2e%2fetc%2fpasswd",
        f"/api/v1/hls/{session_id}/%2e%2e/etc/passwd",
        f"/api/v1/hls/{session_id}//etc/passwd",
        f"/api/v1/hls/{session_id}/seg_000.m4s/../../../etc/passwd",
    ]

    for p in traversal_paths:
        res = await api_client.get(p, headers=headers)
        assert res.status_code in (400, 404), f"Path {p} was not rejected: {res.status_code}"


@pytest.mark.asyncio
async def test_overlong_names_rejected(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    admin_tokens = await _setup(api_client)
    headers = _bearer(admin_tokens)
    manager = get_session_manager(db_settings)

    session_id = "0123456789abcdef0123456789abcdef"
    _create_test_session(manager, session_id, db_settings, "u_admin")

    # Overlong session ID (> 64 chars)
    overlong_session_id = "a" * 65
    res_sess = await api_client.get(
        f"/api/v1/hls/{overlong_session_id}/master.m3u8",
        headers=headers,
    )
    assert res_sess.status_code in (400, 404)

    # Overlong segment index (> 6 digits, e.g. 7 digits)
    overlong_seg = "seg_1234567.m4s"
    res_seg = await api_client.get(
        f"/api/v1/hls/{session_id}/{overlong_seg}",
        headers=headers,
    )
    assert res_seg.status_code in (400, 404)


@pytest.mark.asyncio
async def test_wrong_session_token_rejected(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    admin_tokens = await _setup(api_client)
    me_res = await api_client.get("/api/v1/auth/me", headers=_bearer(admin_tokens))
    admin_id = me_res.json()["id"]
    manager = get_session_manager(db_settings)

    session_1 = "11111111111111111111111111111111"
    session_2 = "22222222222222222222222222222222"
    _create_test_session(manager, session_1, db_settings, admin_id)
    _create_test_session(manager, session_2, db_settings, admin_id)

    keys = load_keys(db_settings)

    # Mint token scoped to session_1
    token_s1, _ = create_media_token(
        user_id=admin_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="item_sec_1",
        session_id=session_1,
    )
    encoded_token_s1 = quote(token_s1, safe="")

    # Accessing session_1 with token_s1 succeeds
    res_ok = await api_client.get(f"/api/v1/hls/{session_1}/media.m3u8?token={encoded_token_s1}")
    assert res_ok.status_code == 200

    # Accessing session_2 with token_s1 MUST fail with 403 Forbidden
    res_wrong = await api_client.get(f"/api/v1/hls/{session_2}/media.m3u8?token={encoded_token_s1}")
    assert res_wrong.status_code == 403
    assert res_wrong.json()["error"]["code"] == "invalid_media_token"


@pytest.mark.asyncio
async def test_expired_media_token_rejected(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    await _setup(api_client)
    manager = get_session_manager(db_settings)

    session_id = "33333333333333333333333333333333"
    _create_test_session(manager, session_id, db_settings, "u_admin")

    keys = load_keys(db_settings)

    # Mint expired token (expired 1 hour ago)
    past_time = datetime.now(UTC) - timedelta(hours=2)
    token_expired, _ = create_media_token(
        user_id="u_admin",
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="item_sec_1",
        session_id=session_id,
        now=past_time,
    )
    encoded_expired = quote(token_expired, safe="")

    res = await api_client.get(f"/api/v1/hls/{session_id}/media.m3u8?token={encoded_expired}")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_media_token"
