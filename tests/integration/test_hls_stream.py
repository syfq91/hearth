"""Integration tests for HLS fMP4 delivery endpoints."""

import asyncio
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from app.core.config import Settings
from app.core.secrets import load_keys
from app.core.tokens import create_media_token
from app.services.media.probe import ProbeStream
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


@pytest.mark.asyncio
async def test_hls_vod_playlist_and_segment_seeking(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    tmp_path: Path,
) -> None:
    admin_tokens = await _setup(api_client)
    manager = get_session_manager(db_settings)
    session_id = "test_vod_session_456"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Initial segments written by transcode
    (out_dir / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypiso5")
    (out_dir / "seg_000.m4s").write_bytes(b"\x00\x00\x00\x10moof")
    (out_dir / "media.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        target_width=1280,
        target_height=720,
        target_bitrate=2_000_000,
        hwaccel="none",
    )

    me_res = await api_client.get("/api/v1/auth/me", headers=_bearer(admin_tokens))
    admin_id = me_res.json()["id"]

    # Session with 14.5s total duration (3 full 4s segments + 1 remaining 2.5s segment = 4 segments)
    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_vod",
        file_id="file_vod",
        input_path=Path("/media/show.mkv"),
        output_dir=out_dir,
        plan=plan,
        settings=db_settings,
        duration_seconds=14.5,
    )
    manager.sessions[session_id] = session

    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=admin_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="item_vod",
    )
    encoded_token = quote(token, safe="")

    # Fetch media playlist - should be full VOD playlist
    media_res = await api_client.get(f"/api/v1/hls/{session_id}/media.m3u8?token={encoded_token}")
    assert media_res.status_code == 200
    media_text = media_res.text
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in media_text
    assert "#EXT-X-ENDLIST" in media_text
    assert f"seg_000.m4s?token={encoded_token}" in media_text
    assert f"seg_001.m4s?token={encoded_token}" in media_text
    assert f"seg_002.m4s?token={encoded_token}" in media_text
    assert f"seg_003.m4s?token={encoded_token}" in media_text
    assert "#EXTINF:2.500," in media_text

    # Pre-populate seg_002 to simulate seek completion
    (out_dir / "seg_002.m4s").write_bytes(b"\x00\x00\x00\x10moof_seek")

    # Fetch seeked segment
    seg_res = await api_client.get(f"/api/v1/hls/{session_id}/seg_002.m4s?token={encoded_token}")
    assert seg_res.status_code == 200
    assert seg_res.content == b"\x00\x00\x00\x10moof_seek"

    await manager.stop_all()


@pytest.mark.asyncio
async def test_hls_segment_endpoint_ignores_tmp_files(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    """T01 acceptance: segment endpoint ignores .tmp files and treats only final names as available.

    Ties to INV-5 (atomic segment visibility) and INV-10 (strict path regex).
    """
    admin_tokens = await _setup(api_client)
    manager = get_session_manager(db_settings)
    session_id = "test_atomic_tmp_session"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Place an in-progress temporary segment and a completed initialization segment
    (out_dir / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypiso5")
    (out_dir / "seg_000.m4s.tmp").write_bytes(b"\x00\x00\x00\x10partial_data")
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

    me_res = await api_client.get("/api/v1/auth/me", headers=_bearer(admin_tokens))
    admin_id = me_res.json()["id"]

    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_tmp_test",
        file_id="file_tmp_test",
        input_path=Path("/media/test.mp4"),
        output_dir=out_dir,
        plan=plan,
        settings=db_settings,
    )
    manager.sessions[session_id] = session

    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=admin_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="item_tmp_test",
    )
    encoded_token = quote(token, safe="")

    # 1. Requesting a .tmp file directly is rejected with 404 invalid_segment (INV-10)
    res_direct_tmp = await api_client.get(
        f"/api/v1/hls/{session_id}/seg_000.m4s.tmp?token={encoded_token}"
    )
    assert res_direct_tmp.status_code == 404
    assert res_direct_tmp.json()["error"]["code"] == "invalid_segment"

    # 2. Requesting seg_000.m4s when only seg_000.m4s.tmp exists does not return .tmp data.
    # Since session process is not running, ensure_segment times out and returns 503
    # with Retry-After (INV-7)
    res_seg = await api_client.get(f"/api/v1/hls/{session_id}/seg_000.m4s?token={encoded_token}")
    assert res_seg.status_code == 503
    assert res_seg.headers.get("Retry-After") == "1"
    assert res_seg.json()["error"]["code"] == "transcode_timeout"

    # 3. Simulate atomic rename: seg_000.m4s.tmp is renamed to seg_000.m4s
    (out_dir / "seg_000.m4s.tmp").unlink()
    (out_dir / "seg_000.m4s").write_bytes(b"\x00\x00\x00\x10final_complete_data")

    # 4. Now requesting seg_000.m4s succeeds and delivers the completed segment
    res_complete = await api_client.get(
        f"/api/v1/hls/{session_id}/seg_000.m4s?token={encoded_token}"
    )
    assert res_complete.status_code == 200
    assert res_complete.content == b"\x00\x00\x00\x10final_complete_data"

    await manager.stop_all()


@pytest.mark.asyncio
async def test_master_playlist_dynamic_codecs_non_high_source(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    tmp_path: Path,
) -> None:
    """T08 acceptance: master playlist asserts no hardcoded avc1.640028 remains

    for non-High/4.0 sources, and real BANDWIDTH/RESOLUTION/FRAME-RATE are emitted.
    """
    admin_tokens = await _setup(api_client)
    manager = get_session_manager(db_settings)
    session_id = "test_non_high_codecs"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write dummy initial segment files so wait_for_first_segment succeeds
    (out_dir / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypiso5")
    (out_dir / "media.m3u8").write_text("#EXTM3U\n#EXT-X-VERSION:7\n", encoding="utf-8")

    # Copied HEVC Main L93 video + Copied E-AC-3 audio
    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=6,
        subtitle_action="none",
        target_width=1280,
        target_height=720,
        target_bitrate=3_500_000,
        hwaccel="none",
    )

    src_video = ProbeStream(
        index=0,
        codec_type="video",
        codec_name="hevc",
        profile="Main",
        level=93,
        width=1280,
        height=720,
    )
    src_audio = ProbeStream(
        index=1,
        codec_type="audio",
        codec_name="eac3",
        channels=6,
    )

    me_res = await api_client.get("/api/v1/auth/me", headers=_bearer(admin_tokens))
    admin_id = me_res.json()["id"]

    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_hevc",
        file_id="file_hevc",
        input_path=Path("/media/hevc.mkv"),
        output_dir=out_dir,
        plan=plan,
        settings=db_settings,
        source_video=src_video,
        source_audio=src_audio,
        source_fps=23.976,
    )
    manager.sessions[session_id] = session

    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=admin_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="item_hevc",
    )
    encoded_token = quote(token, safe="")

    res = await api_client.get(f"/api/v1/hls/{session_id}/master.m3u8?token={encoded_token}")
    assert res.status_code == 200
    master_text = res.text

    # Dynamic RFC 6381 codec strings
    assert 'CODECS="hvc1.1.6.L93.B0,ec-3"' in master_text
    # No hardcoded avc1.640028 remains
    assert "avc1.640028" not in master_text
    assert "BANDWIDTH=3500000" in master_text
    assert "AVERAGE-BANDWIDTH=3500000" in master_text
    assert "RESOLUTION=1280x720" in master_text
    assert "FRAME-RATE=23.976" in master_text

    await manager.stop_all()


@pytest.mark.asyncio
async def test_segment_streaming_active_request_accounting_and_reaper(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    tmp_path: Path,
) -> None:
    from datetime import timedelta

    from app.db.base import utcnow

    admin_tokens = await _setup(api_client)
    me_res = await api_client.get("/api/v1/auth/me", headers=_bearer(admin_tokens))
    admin_id = me_res.json()["id"]

    manager = get_session_manager(db_settings)
    session_id = "test_accounting_session"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1 MB segment data
    seg_data = b"X" * (1024 * 1024)
    (out_dir / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypiso5")
    (out_dir / "seg_000.m4s").write_bytes(seg_data)

    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="transcode",
        audio_codec="aac",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_accounting",
        file_id="file_accounting",
        input_path=Path("/media/test.mp4"),
        output_dir=out_dir,
        plan=plan,
        settings=db_settings,
        duration_seconds=60.0,
    )
    manager.sessions[session_id] = session

    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=admin_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="item_accounting",
        session_id=session_id,
    )
    encoded_token = quote(token, safe="")

    assert session.active_request_count == 0

    # 1. Normal HTTP request: completes and decrements active_request_count back to 0
    res = await api_client.get(f"/api/v1/hls/{session_id}/seg_000.m4s?token={encoded_token}")
    assert res.status_code == 200
    assert len(res.content) == len(seg_data)
    assert session.active_request_count == 0

    # 2. Test active streaming and reaper protection during slow download
    from app.api.v1.hls import TrackedFileResponse

    session.increment_request()
    tracked_resp = TrackedFileResponse(session=session, path=out_dir / "seg_000.m4s")
    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/api/v1/hls/{session_id}/seg_000.m4s",
        "headers": [],
        "asgi": {"version": "3.0", "spec_version": "2.4"},
    }

    reaped_during_stream = None
    active_during_stream = None

    async def dummy_receive() -> dict[str, str]:
        return {"type": "http.request"}

    async def slow_send(message: dict[str, object]) -> None:
        nonlocal reaped_during_stream, active_during_stream
        if message.get("type") == "http.response.body":
            active_during_stream = session.active_request_count
            # Simulate idle timeout passing mid-slow-download
            session.last_activity_at = utcnow() - timedelta(seconds=70)
            reaped_during_stream = await manager.reap_idle_sessions()

    await tracked_resp(scope, dummy_receive, slow_send)

    assert active_during_stream == 1
    assert reaped_during_stream == []
    assert session_id in manager.sessions
    assert session.active_request_count == 0

    # 3. Test client disconnect mid-stream
    session.increment_request()
    disconnect_resp = TrackedFileResponse(session=session, path=out_dir / "seg_000.m4s")

    async def aborting_send(message: dict[str, object]) -> None:
        if message.get("type") == "http.response.body":
            raise asyncio.CancelledError("Client disconnected")

    with pytest.raises(asyncio.CancelledError):
        await disconnect_resp(scope, dummy_receive, aborting_send)

    # Client disconnect must decrement active_request_count back to 0
    assert session.active_request_count == 0

    # 4. Now that active_request_count is 0, idle reaper can reap it
    session.last_activity_at = utcnow() - timedelta(seconds=70)
    reaped = await manager.reap_idle_sessions()
    assert session_id in reaped
    assert session_id not in manager.sessions

    await manager.stop_all()


@pytest.mark.asyncio
async def test_hls_subtitles_pipeline(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    tmp_path: Path,
) -> None:
    """T10: Text subtitles exposed in master.m3u8 as SUBTITLES group
    and served via subtitles.m3u8/subtitles.vtt.
    """
    admin_tokens = await _setup(api_client)
    manager = get_session_manager(db_settings)
    session_id = "test_subtitles_session_789"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    # Initial segments written by transcode
    (out_dir / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypiso5")
    (out_dir / "seg_000.m4s").write_bytes(b"\x00\x00\x00\x10moof")
    (out_dir / "media.m3u8").write_text("#EXTM3U\n", encoding="utf-8")
    (out_dir / "subtitles.vtt").write_text(
        "WEBVTT\n\n00:01.000 --> 00:02.000\nHello from VTT\n", encoding="utf-8"
    )

    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="sidecar_vtt",
        subtitle_stream_index=0,
        target_width=1920,
        target_height=1080,
        target_bitrate=3_000_000,
        hwaccel="none",
    )

    me_res = await api_client.get("/api/v1/auth/me", headers=_bearer(admin_tokens))
    admin_id = me_res.json()["id"]

    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_sub",
        file_id="file_sub",
        input_path=Path("/media/movie.mkv"),
        output_dir=out_dir,
        plan=plan,
        settings=db_settings,
        duration_seconds=60.0,
    )
    manager.sessions[session_id] = session

    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=admin_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="item_sub",
    )
    encoded_token = quote(token, safe="")

    # 1. Master playlist should advertise SUBTITLES group
    master_res = await api_client.get(f"/api/v1/hls/{session_id}/master.m3u8?token={encoded_token}")
    assert master_res.status_code == 200
    master_text = master_res.text
    assert '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs"' in master_text
    assert f'URI="subtitles.m3u8?token={encoded_token}"' in master_text
    assert 'SUBTITLES="subs"' in master_text

    # 2. Subtitle playlist should return VOD playlist pointing to subtitles.vtt
    sub_m3u8_res = await api_client.get(
        f"/api/v1/hls/{session_id}/subtitles.m3u8?token={encoded_token}"
    )
    assert sub_m3u8_res.status_code == 200
    assert sub_m3u8_res.headers["Content-Type"].startswith("application/vnd.apple.mpegurl")
    sub_text = sub_m3u8_res.text
    assert "#EXT-X-PLAYLIST-TYPE:VOD" in sub_text
    assert f"subtitles.vtt?token={encoded_token}" in sub_text
    assert "#EXT-X-ENDLIST" in sub_text

    # 3. Subtitles VTT endpoint should return the WebVTT file
    vtt_res = await api_client.get(f"/api/v1/hls/{session_id}/subtitles.vtt?token={encoded_token}")
    assert vtt_res.status_code == 200
    assert vtt_res.headers["Content-Type"].startswith("text/vtt")
    assert "Hello from VTT" in vtt_res.text

    # 4. Auth checks
    unauth_res = await api_client.get(f"/api/v1/hls/{session_id}/subtitles.m3u8")
    assert unauth_res.status_code == 401

    await manager.stop_all()


@pytest.mark.asyncio
async def test_hls_throttling_integration(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    tmp_path: Path,
) -> None:
    """T06: Supervisor pauses at lead >= 60s; requesting segment resumes when lead <= 20s."""
    admin_tokens = await _setup(api_client)
    manager = get_session_manager(db_settings)
    session_id = "test_throttle_session_integration"
    out_dir = db_settings.transcode_dir / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "init.mp4").write_bytes(b"\x00\x00\x00\x20ftypiso5")
    # 16 segments of 4s = 64s
    for i in range(16):
        (out_dir / f"seg_{i:03d}.m4s").write_bytes(b"\x00\x00\x00\x10moof")
    (out_dir / "media.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        target_width=1280,
        target_height=720,
        target_bitrate=2_000_000,
        hwaccel="none",
    )

    me_res = await api_client.get("/api/v1/auth/me", headers=_bearer(admin_tokens))
    admin_id = me_res.json()["id"]

    session = TranscodeSession(
        session_id=session_id,
        user_id=admin_id,
        item_id="item_throttle",
        file_id="file_throttle",
        input_path=Path("/media/movie.mp4"),
        output_dir=out_dir,
        plan=plan,
        settings=db_settings,
        duration_seconds=7200.0,
    )
    from unittest.mock import AsyncMock, MagicMock

    mock_proc = MagicMock(returncode=None)
    mock_proc.wait = AsyncMock(return_value=0)
    session.process = mock_proc  # type: ignore[assignment]
    manager.sessions[session_id] = session

    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=admin_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="item_throttle",
    )
    encoded_token = quote(token, safe="")

    # 1. Lead is 64s >= 60s -> throttle_sessions pauses session
    assert session.compute_lead() == 64.0
    await manager.throttle_sessions()
    assert session.is_paused is True

    # 2. Client requests seg_011.m4s -> served end becomes 12 * 4 = 48s, lead becomes 16s <= 20s
    seg_res = await api_client.get(f"/api/v1/hls/{session_id}/seg_011.m4s?token={encoded_token}")
    assert seg_res.status_code == 200
    assert session.last_served_segment_end == 48.0
    assert session.compute_lead() == 16.0
    # Immediate resume upon client catchup
    assert session.is_paused is False

    await manager.stop_all()
