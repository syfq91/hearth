"""Integration tests for direct audio streaming with HTTP range requests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.tokens import create_media_token
from app.db.models import Album, Artist, Library, LibraryPath, MediaFile, MediaItem, Track

ADMIN = {"username": "admin", "password": "correct horse"}
NOW = datetime.now(UTC)


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _seed_audio_track(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    content_bytes: bytes = b"A" * 1024 + b"B" * 1024 + b"C" * 1024,
    container: str = "flac",
    duration_seconds: float = 185.5,
) -> tuple[str, str, str, str]:
    """Create a music library with one track and audio file on disk."""
    root = settings.media_dir / f"music_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    audio_path = root / f"track.{container}"
    audio_path.write_bytes(content_bytes)

    lib_id = f"lib_{uuid.uuid4().hex[:8]}"
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    alb_id = f"alb_{uuid.uuid4().hex[:8]}"
    tr_id = f"tr_{uuid.uuid4().hex[:8]}"
    f_id = f"f_{uuid.uuid4().hex[:8]}"

    async with sessionmaker() as session:
        lib = Library(id=lib_id, name="Streaming Lib", type="music")
        lp = LibraryPath(id=f"lp_{uuid.uuid4().hex[:8]}", library_id=lib_id, path=str(root))
        session.add_all([lib, lp])

        art_item = MediaItem(
            id=art_id, library_id=lib_id, type="artist", title="Artist One", sort_title="Artist One"
        )
        art = Artist(item_id=art_id)
        session.add_all([art_item, art])

        alb_item = MediaItem(
            id=alb_id, library_id=lib_id, type="album", title="Album One", sort_title="Album One"
        )
        alb = Album(item_id=alb_id, artist_id=art_id)
        session.add_all([alb_item, alb])

        tr_item = MediaItem(
            id=tr_id,
            library_id=lib_id,
            type="track",
            title="Track One",
            sort_title="Track One",
            runtime_seconds=int(duration_seconds),
        )
        session.add(tr_item)
        await session.flush()

        media_file = MediaFile(
            id=f_id,
            item_id=tr_id,
            path=str(audio_path),
            size_bytes=len(content_bytes),
            mtime=1.0,
            partial_hash=uuid.uuid4().hex * 2,
            container=container,
            duration_seconds=duration_seconds,
        )
        track = Track(
            item_id=tr_id,
            album_id=alb_id,
            artist_id=art_id,
            file_id=f_id,
            disc_number=1,
            track_number=1,
            duration_seconds=duration_seconds,
            bitrate=1411000,
            sample_rate=44100,
            channels=2,
        )
        session.add_all([media_file, track])
        await session.commit()

    return lib_id, tr_id, f_id, str(audio_path)


@pytest.mark.asyncio
async def test_audio_stream_full_and_head(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    data = b"0123456789" * 100  # 1000 bytes
    _, tr_id, f_id, _ = await _seed_audio_track(
        db_settings, db_sessionmaker, content_bytes=data, container="flac"
    )

    # HEAD request
    head_res = await api_client.head(f"/api/v1/stream/audio/{tr_id}", headers=_bearer(admin))
    assert head_res.status_code == 200
    assert head_res.headers["Content-Length"] == "1000"
    assert head_res.headers["Accept-Ranges"] == "bytes"
    assert head_res.headers["Content-Type"] == "audio/flac"
    assert "ETag" in head_res.headers

    # Full GET request
    get_res = await api_client.get(f"/api/v1/stream/audio/{tr_id}", headers=_bearer(admin))
    assert get_res.status_code == 200
    assert get_res.headers["Content-Length"] == "1000"
    assert get_res.headers["Accept-Ranges"] == "bytes"
    assert get_res.content == data

    # Also works via stream/{file_id}
    file_res = await api_client.get(f"/api/v1/stream/{f_id}", headers=_bearer(admin))
    assert file_res.status_code == 200
    assert file_res.content == data


@pytest.mark.asyncio
async def test_audio_stream_range_requests(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    data = b"".join(bytes([i % 256]) for i in range(2000))  # 2000 bytes
    _, tr_id, _, _ = await _seed_audio_track(
        db_settings, db_sessionmaker, content_bytes=data, container="mp3"
    )

    # Range 0-499
    res = await api_client.get(
        f"/api/v1/stream/audio/{tr_id}",
        headers={**_bearer(admin), "Range": "bytes=0-499"},
    )
    assert res.status_code == 206
    assert res.headers["Content-Range"] == "bytes 0-499/2000"
    assert res.headers["Content-Length"] == "500"
    assert res.headers["Content-Type"] == "audio/mpeg"
    assert res.content == data[0:500]

    # Range 500- (from 500 to end)
    res = await api_client.get(
        f"/api/v1/stream/audio/{tr_id}",
        headers={**_bearer(admin), "Range": "bytes=500-"},
    )
    assert res.status_code == 206
    assert res.headers["Content-Range"] == "bytes 500-1999/2000"
    assert res.headers["Content-Length"] == "1500"
    assert res.content == data[500:2000]

    # Suffix range: -300 (last 300 bytes)
    res = await api_client.get(
        f"/api/v1/stream/audio/{tr_id}",
        headers={**_bearer(admin), "Range": "bytes=-300"},
    )
    assert res.status_code == 206
    assert res.headers["Content-Range"] == "bytes 1700-1999/2000"
    assert res.headers["Content-Length"] == "300"
    assert res.content == data[1700:2000]

    # Unsatisfiable range: past end
    res = await api_client.get(
        f"/api/v1/stream/audio/{tr_id}",
        headers={**_bearer(admin), "Range": "bytes=3000-"},
    )
    assert res.status_code == 416
    assert res.headers["Content-Range"] == "bytes */2000"


@pytest.mark.asyncio
async def test_audio_stream_conditional_etag(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    data = b"Audio ETag Content"
    _, tr_id, _, _ = await _seed_audio_track(
        db_settings, db_sessionmaker, content_bytes=data, container="flac"
    )

    res = await api_client.get(f"/api/v1/stream/audio/{tr_id}", headers=_bearer(admin))
    assert res.status_code == 200
    etag = res.headers["ETag"]

    # Conditional GET with matching ETag
    res304 = await api_client.get(
        f"/api/v1/stream/audio/{tr_id}",
        headers={**_bearer(admin), "If-None-Match": etag},
    )
    assert res304.status_code == 304


@pytest.mark.asyncio
async def test_audio_stream_media_token_auth(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    data = b"Protected Audio Stream"
    _, tr_id, f_id, _ = await _seed_audio_track(
        db_settings, db_sessionmaker, content_bytes=data, container="flac"
    )

    # 1. Unauthenticated request without token or bearer returns 401
    res = await api_client.get(f"/api/v1/stream/audio/{tr_id}")
    assert res.status_code == 401

    # 2. Get user info to mint media token
    user_res = await api_client.get("/api/v1/auth/me", headers=_bearer(admin))
    user_info = user_res.json()

    from app.core.secrets import load_keys

    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=user_info["id"],
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id=tr_id,
    )

    # Stream with media token in query parameter (no Authorization header)
    token_res = await api_client.get(f"/api/v1/stream/audio/{tr_id}?token={quote(token, safe='')}")
    assert token_res.status_code == 200
    assert token_res.content == data

    # Stream via file endpoint with item-scoped token
    file_res = await api_client.get(f"/api/v1/stream/{f_id}?token={quote(token, safe='')}")
    assert file_res.status_code == 200
    assert file_res.content == data

    # Token scoped to wrong item ID should fail
    wrong_token, _ = create_media_token(
        user_id=user_info["id"],
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id="wrong_item_id",
    )
    bad_res = await api_client.get(
        f"/api/v1/stream/audio/{tr_id}?token={quote(wrong_token, safe='')}"
    )
    assert bad_res.status_code in (403, 404)
