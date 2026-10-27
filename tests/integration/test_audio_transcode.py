"""Integration tests for direct audio transcoding (FLAC -> Opus/MP3/AAC)."""

import uuid
from urllib.parse import quote

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.secrets import load_keys
from app.core.tokens import create_media_token
from app.db.models import Album, Artist, Library, LibraryPath, MediaFile, MediaItem, Track

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _seed_music_track(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[str, str, str]:
    root = settings.media_dir / f"Music_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    audio_path = root / "track.flac"
    # Write sample audio content
    audio_path.write_bytes(b"fLaC" + (b"\x00" * 4096))

    lib_id = f"lib_{uuid.uuid4().hex[:8]}"
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    alb_id = f"alb_{uuid.uuid4().hex[:8]}"
    trk_id = f"trk_{uuid.uuid4().hex[:8]}"
    file_id = f"file_{uuid.uuid4().hex[:8]}"

    async with sessionmaker() as session:
        session.add(Library(id=lib_id, name="Music Library", type="music"))
        session.add(LibraryPath(id=f"lp_{uuid.uuid4().hex[:8]}", library_id=lib_id, path=str(root)))
        session.add(
            MediaItem(
                id=art_id, library_id=lib_id, type="artist", title="Artist", sort_title="Artist"
            )
        )
        session.add(Artist(item_id=art_id))
        session.add(
            MediaItem(id=alb_id, library_id=lib_id, type="album", title="Album", sort_title="Album")
        )
        session.add(Album(item_id=alb_id, artist_id=art_id))
        session.add(
            MediaItem(id=trk_id, library_id=lib_id, type="track", title="Track", sort_title="Track")
        )
        session.add(
            MediaFile(
                id=file_id,
                item_id=trk_id,
                path=str(audio_path),
                size_bytes=len(audio_path.read_bytes()),
                mtime=audio_path.stat().st_mtime,
                partial_hash=uuid.uuid4().hex * 2,
                container="flac",
                duration_seconds=180.0,
            )
        )
        session.add(
            Track(
                item_id=trk_id,
                album_id=alb_id,
                artist_id=art_id,
                file_id=file_id,
                duration_seconds=180.0,
            )
        )
        await session.commit()

    return lib_id, trk_id, file_id


@pytest.mark.asyncio
async def test_audio_transcoding_endpoints(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin_tokens = await _setup(api_client)
    _, trk_id, file_id = await _seed_music_track(db_settings, db_sessionmaker)

    # 1. Unauthenticated request returns 401
    unauth = await api_client.get(f"/api/v1/stream/audio/{trk_id}/transcode")
    assert unauth.status_code == 401

    # 2. Transcode to Opus via bearer token
    opus_res = await api_client.get(
        f"/api/v1/stream/audio/{trk_id}/transcode?format=opus&bitrate=128",
        headers=_bearer(admin_tokens),
    )
    # If ffmpeg is present, returns 200 with audio/ogg; codecs=opus
    assert opus_res.status_code == 200
    assert "audio/ogg" in opus_res.headers["Content-Type"]

    # 3. Transcode to MP3 via media token
    me_res = await api_client.get("/api/v1/auth/me", headers=_bearer(admin_tokens))
    user_id = me_res.json()["id"]
    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=user_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id=trk_id,
    )
    encoded_token = quote(token, safe="")

    mp3_res = await api_client.get(
        f"/api/v1/stream/audio/{trk_id}/transcode?format=mp3&bitrate=192&token={encoded_token}"
    )
    assert mp3_res.status_code == 200
    assert "audio/mpeg" in mp3_res.headers["Content-Type"]

    # 4. Transcode to AAC via file_id endpoint
    aac_res = await api_client.get(
        f"/api/v1/stream/{file_id}/transcode?format=aac&bitrate=256",
        headers=_bearer(admin_tokens),
    )
    assert aac_res.status_code == 200
    assert "audio/aac" in aac_res.headers["Content-Type"]
