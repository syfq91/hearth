"""Integration tests for music play tracking, favorites, ratings, and ListenBrainz."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Album,
    Artist,
    Job,
    Library,
    LibraryPath,
    MediaFile,
    MediaItem,
    Track,
)
from app.services.jobs.handlers import REGISTRY
from app.services.jobs.types import JobContext
from app.services.scrobbling.listenbrainz import LISTENBRAINZ_SCROBBLE_JOB_TYPE

ADMIN = {"username": "admin", "password": "correct horse"}
NOW = datetime.now(UTC)


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _seed_music_track(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    duration_seconds: float = 200.0,
) -> tuple[str, str, str, str]:
    """Create a music library with artist, album, track."""
    root = settings.media_dir / f"music_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    audio_path = root / "track.flac"
    audio_path.write_bytes(b"dummy flac data")

    lib_id = f"lib_{uuid.uuid4().hex[:8]}"
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    alb_id = f"alb_{uuid.uuid4().hex[:8]}"
    tr_id = f"tr_{uuid.uuid4().hex[:8]}"
    f_id = f"f_{uuid.uuid4().hex[:8]}"

    async with sessionmaker() as session:
        lib = Library(id=lib_id, name="Music Lib", type="music")
        lp = LibraryPath(id=f"lp_{uuid.uuid4().hex[:8]}", library_id=lib_id, path=str(root))
        session.add_all([lib, lp])

        art_item = MediaItem(
            id=art_id, library_id=lib_id, type="artist", title="Daft Punk", sort_title="Daft Punk"
        )
        art = Artist(
            item_id=art_id,
            musicbrainz_id="056e4f3e-d505-4dad-8ec1-d04f521cbb56",
        )
        session.add_all([art_item, art])

        alb_item = MediaItem(
            id=alb_id, library_id=lib_id, type="album", title="Discovery", sort_title="Discovery"
        )
        alb = Album(
            item_id=alb_id,
            artist_id=art_id,
            album_artist="Daft Punk",
            musicbrainz_id="d1887e2a-d9aa-4034-8c85-6f6eb80cb4a5",
        )
        session.add_all([alb_item, alb])

        tr_item = MediaItem(
            id=tr_id,
            library_id=lib_id,
            type="track",
            title="One More Time",
            sort_title="One More Time",
            runtime_seconds=int(duration_seconds),
        )
        session.add(tr_item)
        await session.flush()

        media_file = MediaFile(
            id=f_id,
            item_id=tr_id,
            path=str(audio_path),
            size_bytes=1024,
            mtime=1.0,
            partial_hash=uuid.uuid4().hex * 2,
            container="flac",
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
            musicbrainz_track_id="rec-1111-2222",
        )
        session.add_all([media_file, track])
        await session.commit()

    return lib_id, art_id, alb_id, tr_id


@pytest.mark.asyncio
async def test_listenbrainz_user_token_crud(
    api_client: httpx.AsyncClient,
) -> None:
    admin = await _setup(api_client)

    # Initially not configured
    res = await api_client.get("/api/v1/users/me/listenbrainz", headers=_bearer(admin))
    assert res.status_code == 200
    assert res.json() == {"configured": False}

    # Set token
    res = await api_client.put(
        "/api/v1/users/me/listenbrainz",
        json={"token": "test-user-token-12345"},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    assert res.json() == {"configured": True}

    # Verify configured
    res = await api_client.get("/api/v1/users/me/listenbrainz", headers=_bearer(admin))
    assert res.status_code == 200
    assert res.json() == {"configured": True}

    # Delete token
    res = await api_client.delete("/api/v1/users/me/listenbrainz", headers=_bearer(admin))
    assert res.status_code == 204

    # Verify cleared
    res = await api_client.get("/api/v1/users/me/listenbrainz", headers=_bearer(admin))
    assert res.status_code == 200
    assert res.json() == {"configured": False}


@pytest.mark.asyncio
async def test_track_progress_and_listenbrainz_scrobble_queue(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, _, alb_id, tr_id = await _seed_music_track(
        db_settings, db_sessionmaker, duration_seconds=200.0
    )

    # 1. Configure ListenBrainz token for admin
    await api_client.put(
        "/api/v1/users/me/listenbrainz",
        json={"token": "my-secret-token"},
        headers=_bearer(admin),
    )

    # 2. Report playing checkpoint (10 seconds in)
    res = await api_client.post(
        f"/api/v1/items/{tr_id}/progress",
        json={"position_ms": 10000, "state": "playing"},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["play_count"] == 1
    assert body["watched"] is False
    assert body["auto_watched"] is False
    assert body["position_ms"] == 10000

    # 3. Report completion checkpoint (185 seconds in out of 200s, > 90% watched_threshold)
    res = await api_client.post(
        f"/api/v1/items/{tr_id}/progress",
        json={"position_ms": 185000, "state": "playing"},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["watched"] is True
    assert body["auto_watched"] is True

    # 4. Check that a scrobble.listenbrainz job was enqueued in the database
    async with db_sessionmaker() as session:
        jobs = list(
            await session.scalars(select(Job).where(Job.type == LISTENBRAINZ_SCROBBLE_JOB_TYPE))
        )
        assert len(jobs) == 1
        scrobble_job = jobs[0]
        assert scrobble_job.payload["track_id"] == tr_id
        assert "user_id" in scrobble_job.payload
        assert "listened_at" in scrobble_job.payload

        # 5. Execute the background job handler using mock HTTP server
        spec = REGISTRY.get(LISTENBRAINZ_SCROBBLE_JOB_TYPE)
        assert spec is not None

        # Mock the ListenBrainz API endpoint
        submitted: list[dict] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            import json

            submitted.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(mock_handler)

        # Patch AsyncClient in listenbrainz module temporarily
        import app.services.scrobbling.listenbrainz as lb_mod

        old_submit = lb_mod.submit_listen

        async def patched_submit(*args, **kwargs):
            mock_client = httpx.AsyncClient(transport=transport)
            kwargs["client"] = mock_client
            return await old_submit(*args, **kwargs)

        lb_mod.submit_listen = patched_submit
        try:
            context = JobContext(
                job_id=scrobble_job.id,
                payload=scrobble_job.payload,
                settings=db_settings,
                sessionmaker=db_sessionmaker,
                report_progress=lambda p: None,
                is_cancelled=lambda: False,
            )
            result = await spec.handler(context)
            assert result["status"] == "ok"
            assert len(submitted) == 1
            listen = submitted[0]["payload"][0]
            assert listen["track_metadata"]["track_name"] == "One More Time"
            assert listen["track_metadata"]["artist_name"] == "Daft Punk"
            assert listen["track_metadata"]["release_name"] == "Discovery"
        finally:
            lb_mod.submit_listen = old_submit


@pytest.mark.asyncio
async def test_favorites_and_ratings_on_music(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, art_id, alb_id, tr_id = await _seed_music_track(db_settings, db_sessionmaker)

    # Favorite artist
    res = await api_client.post(
        f"/api/v1/items/{art_id}/favorite",
        json={"favorite": True},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    assert res.json()["favorite"] is True

    # Rate album
    res = await api_client.put(
        f"/api/v1/items/{alb_id}/rating",
        json={"rating": 9},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    assert res.json()["rating"] == 9

    # Favorite track
    res = await api_client.post(
        f"/api/v1/items/{tr_id}/favorite",
        json={"favorite": True},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    assert res.json()["favorite"] is True

    # Check track detail reflects favorite
    res = await api_client.get(f"/api/v1/tracks/{tr_id}", headers=_bearer(admin))
    assert res.status_code == 200
    track_detail = res.json()
    assert track_detail["watch_state"]["favorite"] is True

    # Check album detail reflects rating
    res = await api_client.get(f"/api/v1/albums/{alb_id}", headers=_bearer(admin))
    assert res.status_code == 200
    album_detail = res.json()
    assert album_detail["watch_state"]["rating"] == 9


@pytest.mark.asyncio
async def test_album_wide_mark_watched(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, _, alb_id, tr_id = await _seed_music_track(db_settings, db_sessionmaker)

    # Mark album watched
    res = await api_client.post(
        f"/api/v1/items/{alb_id}/watched",
        json={"watched": True},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    assert res.json()["watched"] is True

    # Track should now be marked watched
    res = await api_client.get(f"/api/v1/tracks/{tr_id}", headers=_bearer(admin))
    assert res.status_code == 200
    assert res.json()["watch_state"]["watched"] is True

    # Most-played shelf should now return the album and track
    res = await api_client.get("/api/v1/music/most-played", headers=_bearer(admin))
    assert res.status_code == 200
    most_played = res.json()
    assert any(a["id"] == alb_id for a in most_played["albums"])
    assert any(t["id"] == tr_id for t in most_played["tracks"])
