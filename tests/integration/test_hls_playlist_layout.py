"""Integration tests for HLS playlists built from the session segment grid."""

from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.services.transcode.decision import TranscodePlan
from app.services.transcode.session import TranscodeSession, get_session_manager

ADMIN = {"username": "admin", "password": "correct horse"}


async def _headers(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _plan() -> TranscodePlan:
    return TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )


def _seed_session(settings: Settings) -> TranscodeSession:
    manager = get_session_manager(settings)
    output_dir = settings.transcode_dir / "playlist_layout_session"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "init.mp4").write_bytes(b"init")
    (output_dir / "media.m3u8").write_text("#EXTM3U\n", encoding="utf-8")

    session = TranscodeSession(
        session_id="playlist_layout_session",
        user_id="user_admin",
        item_id="item_1",
        file_id="file_1",
        input_path=Path("/media/source.mkv"),
        output_dir=output_dir,
        plan=_plan(),
        settings=settings,
        duration_seconds=20.0,
    )
    manager.sessions[session.id] = session
    return session


def _extinf(content: str) -> list[float]:
    return [
        float(line.split(":", 1)[1].rstrip(","))
        for line in content.splitlines()
        if line.startswith("#EXTINF:")
    ]


@pytest.mark.asyncio
async def test_media_playlist_reflects_short_opening_segment(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    headers = await _headers(api_client)
    db_settings.transcode_segment_duration_seconds = 4
    db_settings.transcode_first_segment_duration_seconds = 2.0
    session = _seed_session(db_settings)

    response = await api_client.get(
        f"/api/v1/hls/{session.id}/media.m3u8",
        headers=headers,
    )
    assert response.status_code == 200, response.text

    content = response.text
    assert "#EXT-X-TARGETDURATION:4" in content
    # 20s of media: 2s opener, four 4s segments, and a 2s tail.
    assert _extinf(content) == pytest.approx([2.0, 4.0, 4.0, 4.0, 4.0, 2.0])
    assert "seg_005.m4s" in content
    assert "seg_006.m4s" not in content

    out_of_range = await api_client.get(
        f"/api/v1/hls/{session.id}/seg_006.m4s",
        headers=headers,
    )
    assert out_of_range.status_code == 404
    assert out_of_range.json()["error"]["code"] == "segment_out_of_range"


@pytest.mark.asyncio
async def test_media_playlist_uniform_when_short_opener_disabled(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
) -> None:
    headers = await _headers(api_client)
    db_settings.transcode_segment_duration_seconds = 4
    db_settings.transcode_first_segment_duration_seconds = 0.0
    session = _seed_session(db_settings)

    response = await api_client.get(
        f"/api/v1/hls/{session.id}/media.m3u8",
        headers=headers,
    )
    assert response.status_code == 200, response.text

    content = response.text
    assert "#EXT-X-TARGETDURATION:4" in content
    assert _extinf(content) == pytest.approx([4.0, 4.0, 4.0, 4.0, 4.0])
