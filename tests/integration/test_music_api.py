"""Integration tests for native music and playlist API endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Album,
    Artist,
    Genre,
    ItemGenre,
    Library,
    LibraryPath,
    MediaFile,
    MediaItem,
    Track,
)
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}
NOW = datetime.now(UTC)


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _add_user(
    client: httpx.AsyncClient, admin: dict[str, str], username: str, library_ids: list[str]
) -> dict[str, str]:
    created = await client.post(
        "/api/v1/users",
        json={
            "username": username,
            "password": "correct horse",
            "role": "user",
            "library_ids": library_ids,
        },
        headers=_bearer(admin),
    )
    assert created.status_code == 201, created.text
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "correct horse"}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _seed_music_data(
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession]
) -> tuple[str, dict[str, str]]:
    """Seed test artists, albums, tracks, and genre data."""
    root = settings.media_dir / f"music_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)

    lib_id = f"lib_{uuid.uuid4().hex[:8]}"
    async with sessionmaker() as session:
        lib = Library(id=lib_id, name="Test Music", type="music")
        session.add(lib)
        session.add(
            LibraryPath(
                id=f"lp_{uuid.uuid4().hex[:8]}",
                library_id=lib_id,
                path=str(root),
            )
        )

        rock_genre = Genre(id="genre_rock", name="Rock")
        electronic_genre = Genre(id="genre_electronic", name="Electronic")
        session.add_all([rock_genre, electronic_genre])
        await session.flush()

        # Artist 1: Radiohead
        art1_id = f"art_{uuid.uuid4().hex[:8]}"
        art1_item = MediaItem(
            id=art1_id,
            library_id=lib_id,
            type="artist",
            title="Radiohead",
            sort_title=sort_title("Radiohead"),
            overview="English rock band from Abingdon, Oxfordshire.",
            added_at=NOW,
            updated_at=NOW,
        )
        art1 = Artist(
            item_id=art1_id,
            sort_name="Radiohead",
            country="GB",
            musicbrainz_id="a74b1b7f-71a5-4011-9441-d0b5e4122711",
        )
        session.add_all([art1_item, art1])

        # Artist 2: Daft Punk
        art2_id = f"art_{uuid.uuid4().hex[:8]}"
        art2_item = MediaItem(
            id=art2_id,
            library_id=lib_id,
            type="artist",
            title="Daft Punk",
            sort_title=sort_title("Daft Punk"),
            overview="French electronic music duo.",
            added_at=NOW,
            updated_at=NOW,
        )
        art2 = Artist(
            item_id=art2_id,
            sort_name="Daft Punk",
            country="FR",
            musicbrainz_id="056e4f3e-d505-4dad-8ec1-d04f521cbb56",
        )
        session.add_all([art2_item, art2])

        # Album 1: OK Computer (Radiohead)
        alb1_id = f"alb_{uuid.uuid4().hex[:8]}"
        alb1_item = MediaItem(
            id=alb1_id,
            library_id=lib_id,
            type="album",
            title="OK Computer",
            sort_title=sort_title("OK Computer"),
            year=1997,
            added_at=NOW,
            updated_at=NOW,
        )
        alb1 = Album(
            item_id=alb1_id,
            artist_id=art1_id,
            album_artist="Radiohead",
            release_date="1997-05-21",
            total_discs=1,
            total_tracks=2,
            musicbrainz_id="b6da530d-3d2b-4e09-9799-f2172a500734",
        )
        session.add_all([alb1_item, alb1])

        # Album 2: Discovery (Daft Punk)
        alb2_id = f"alb_{uuid.uuid4().hex[:8]}"
        alb2_item = MediaItem(
            id=alb2_id,
            library_id=lib_id,
            type="album",
            title="Discovery",
            sort_title=sort_title("Discovery"),
            year=2001,
            added_at=NOW,
            updated_at=NOW,
        )
        alb2 = Album(
            item_id=alb2_id,
            artist_id=art2_id,
            album_artist="Daft Punk",
            release_date="2001-03-12",
            total_discs=1,
            total_tracks=1,
            musicbrainz_id="d1887e2a-d9aa-4034-8c85-6f6eb80cb4a5",
        )
        session.add_all([alb2_item, alb2])
        await session.flush()

        session.add(ItemGenre(item_id=alb1_id, genre_id="genre_rock"))
        session.add(ItemGenre(item_id=alb2_id, genre_id="genre_electronic"))

        # Album 3: Multi-disc Compilation
        alb3_id = f"alb_{uuid.uuid4().hex[:8]}"
        alb3_item = MediaItem(
            id=alb3_id,
            library_id=lib_id,
            type="album",
            title="Festival Hits",
            sort_title=sort_title("Festival Hits"),
            year=2022,
            added_at=NOW,
            updated_at=NOW,
        )
        alb3 = Album(
            item_id=alb3_id,
            artist_id=None,
            album_artist="Various Artists",
            release_date="2022-01-01",
            total_discs=2,
            total_tracks=2,
        )
        session.add_all([alb3_item, alb3])

        # Files and tracks
        # Track 1: Airbag (OK Computer)
        f1_id = f"f_{uuid.uuid4().hex[:8]}"
        f1_path = root / "Radiohead" / "OK Computer" / "01 Airbag.flac"
        f1_path.parent.mkdir(parents=True, exist_ok=True)
        f1_path.write_bytes(b"dummy flac content")
        tr1_id = f"tr_{uuid.uuid4().hex[:8]}"
        tr1_item = MediaItem(
            id=tr1_id,
            library_id=lib_id,
            type="track",
            title="Airbag",
            sort_title=sort_title("Airbag"),
            year=1997,
            added_at=NOW,
            updated_at=NOW,
        )
        file1 = MediaFile(
            id=f1_id,
            item_id=tr1_id,
            path=str(f1_path),
            container="flac",
            size_bytes=1024,
            mtime=1.0,
            partial_hash=uuid.uuid4().hex * 2,
            duration_seconds=284.0,
        )
        tr1 = Track(
            item_id=tr1_id,
            album_id=alb1_id,
            artist_id=art1_id,
            file_id=f1_id,
            disc_number=1,
            track_number=1,
            duration_seconds=284.0,
            bitrate=900000,
            sample_rate=44100,
            channels=2,
            replaygain_track_gain=-6.2,
            replaygain_track_peak=0.98,
        )
        session.add_all([tr1_item, file1, tr1])

        # Track 2: Paranoid Android (OK Computer)
        f2_id = f"f_{uuid.uuid4().hex[:8]}"
        f2_path = root / "Radiohead" / "OK Computer" / "02 Paranoid Android.flac"
        f2_path.write_bytes(b"dummy flac 2")
        tr2_id = f"tr_{uuid.uuid4().hex[:8]}"
        tr2_item = MediaItem(
            id=tr2_id,
            library_id=lib_id,
            type="track",
            title="Paranoid Android",
            sort_title=sort_title("Paranoid Android"),
            year=1997,
            added_at=NOW,
            updated_at=NOW,
        )
        file2 = MediaFile(
            id=f2_id,
            item_id=tr2_id,
            path=str(f2_path),
            container="flac",
            size_bytes=2048,
            mtime=1.0,
            partial_hash=uuid.uuid4().hex * 2,
            duration_seconds=383.0,
        )
        tr2 = Track(
            item_id=tr2_id,
            album_id=alb1_id,
            artist_id=art1_id,
            file_id=f2_id,
            disc_number=1,
            track_number=2,
            duration_seconds=383.0,
            bitrate=920000,
            sample_rate=44100,
            channels=2,
        )
        session.add_all([tr2_item, file2, tr2])

        # Track 3: One More Time (Discovery)
        f3_id = f"f_{uuid.uuid4().hex[:8]}"
        f3_path = root / "Daft Punk" / "Discovery" / "01 One More Time.mp3"
        f3_path.parent.mkdir(parents=True, exist_ok=True)
        f3_path.write_bytes(b"dummy mp3")
        tr3_id = f"tr_{uuid.uuid4().hex[:8]}"
        tr3_item = MediaItem(
            id=tr3_id,
            library_id=lib_id,
            type="track",
            title="One More Time",
            sort_title=sort_title("One More Time"),
            year=2001,
            added_at=NOW,
            updated_at=NOW,
        )
        file3 = MediaFile(
            id=f3_id,
            item_id=tr3_id,
            path=str(f3_path),
            container="mp3",
            size_bytes=4096,
            mtime=1.0,
            partial_hash=uuid.uuid4().hex * 2,
            duration_seconds=320.0,
        )
        tr3 = Track(
            item_id=tr3_id,
            album_id=alb2_id,
            artist_id=art2_id,
            file_id=f3_id,
            disc_number=1,
            track_number=1,
            duration_seconds=320.0,
            bitrate=320000,
            sample_rate=44100,
            channels=2,
        )
        session.add_all([tr3_item, file3, tr3])

        # Compilation Disc 1 Track
        f4_id = f"f_{uuid.uuid4().hex[:8]}"
        f4_path = root / "VA" / "01-01 Rock Intro.mp3"
        f4_path.parent.mkdir(parents=True, exist_ok=True)
        f4_path.write_bytes(b"disc 1 track")
        tr4_id = f"tr_{uuid.uuid4().hex[:8]}"
        tr4_item = MediaItem(
            id=tr4_id,
            library_id=lib_id,
            type="track",
            title="Rock Intro",
            sort_title=sort_title("Rock Intro"),
            year=2022,
            added_at=NOW,
            updated_at=NOW,
        )
        file4 = MediaFile(
            id=f4_id,
            item_id=tr4_id,
            path=str(f4_path),
            container="mp3",
            size_bytes=1000,
            mtime=1.0,
            partial_hash=uuid.uuid4().hex * 2,
            duration_seconds=120.0,
        )
        tr4 = Track(
            item_id=tr4_id,
            album_id=alb3_id,
            artist_id=art1_id,
            file_id=f4_id,
            disc_number=1,
            track_number=1,
            duration_seconds=120.0,
        )
        session.add_all([tr4_item, file4, tr4])

        # Compilation Disc 2 Track
        f5_id = f"f_{uuid.uuid4().hex[:8]}"
        f5_path = root / "VA" / "02-01 Electronic Anthem.mp3"
        f5_path.write_bytes(b"disc 2 track")
        tr5_id = f"tr_{uuid.uuid4().hex[:8]}"
        tr5_item = MediaItem(
            id=tr5_id,
            library_id=lib_id,
            type="track",
            title="Electronic Anthem",
            sort_title=sort_title("Electronic Anthem"),
            year=2022,
            added_at=NOW,
            updated_at=NOW,
        )
        file5 = MediaFile(
            id=f5_id,
            item_id=tr5_id,
            path=str(f5_path),
            container="mp3",
            size_bytes=1000,
            mtime=1.0,
            partial_hash=uuid.uuid4().hex * 2,
            duration_seconds=200.0,
        )
        tr5 = Track(
            item_id=tr5_id,
            album_id=alb3_id,
            artist_id=art2_id,
            file_id=f5_id,
            disc_number=2,
            track_number=1,
            duration_seconds=200.0,
        )
        session.add_all([tr5_item, file5, tr5])

        await session.commit()

    ids = {
        "art1_id": art1_id,
        "art2_id": art2_id,
        "alb1_id": alb1_id,
        "alb2_id": alb2_id,
        "alb3_id": alb3_id,
        "tr1_id": tr1_id,
        "tr2_id": tr2_id,
        "tr3_id": tr3_id,
        "tr4_id": tr4_id,
        "tr5_id": tr5_id,
    }
    return lib_id, ids


@pytest.mark.asyncio
async def test_artists_api(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, ids = await _seed_music_data(db_settings, db_sessionmaker)

    # List artists default
    res = await api_client.get("/api/v1/artists", headers=_bearer(admin))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["total"] == 2
    items = data["items"]
    names = [i["name"] for i in items]
    assert names == ["Daft Punk", "Radiohead"]
    # Check counts
    art1_summary = next(i for i in items if i["id"] == ids["art1_id"])
    assert art1_summary["album_count"] == 1
    assert art1_summary["track_count"] == 3  # tr1, tr2, tr4

    # Search filter
    res = await api_client.get("/api/v1/artists?query=radio", headers=_bearer(admin))
    assert res.status_code == 200
    assert res.json()["total"] == 1
    assert res.json()["items"][0]["name"] == "Radiohead"

    # Artist detail
    res = await api_client.get(f"/api/v1/artists/{ids['art1_id']}", headers=_bearer(admin))
    assert res.status_code == 200
    artist = res.json()
    assert artist["name"] == "Radiohead"
    assert artist["country"] == "GB"
    assert len(artist["albums"]) == 1
    assert artist["albums"][0]["title"] == "OK Computer"
    assert len(artist["top_tracks"]) >= 2


@pytest.mark.asyncio
async def test_albums_api(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, ids = await _seed_music_data(db_settings, db_sessionmaker)

    # List albums
    res = await api_client.get("/api/v1/albums", headers=_bearer(admin))
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 3
    titles = [a["title"] for a in data["items"]]
    assert "Discovery" in titles
    assert "OK Computer" in titles
    assert "Festival Hits" in titles

    # Filter by artist
    res = await api_client.get(f"/api/v1/albums?artist_id={ids['art2_id']}", headers=_bearer(admin))
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "Discovery"

    # Filter by genre
    res = await api_client.get("/api/v1/albums?genre=Rock", headers=_bearer(admin))
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["title"] == "OK Computer"

    # Album detail multi-disc grouping
    res = await api_client.get(f"/api/v1/albums/{ids['alb3_id']}", headers=_bearer(admin))
    assert res.status_code == 200
    album = res.json()
    assert album["title"] == "Festival Hits"
    assert len(album["discs"]) == 2
    assert album["discs"][0]["disc_number"] == 1
    assert len(album["discs"][0]["tracks"]) == 1
    assert album["discs"][0]["tracks"][0]["title"] == "Rock Intro"
    assert album["discs"][1]["disc_number"] == 2
    assert album["discs"][1]["tracks"][0]["title"] == "Electronic Anthem"


@pytest.mark.asyncio
async def test_tracks_api(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, ids = await _seed_music_data(db_settings, db_sessionmaker)

    # List tracks
    res = await api_client.get("/api/v1/tracks", headers=_bearer(admin))
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 5

    # Filter by album
    res = await api_client.get(f"/api/v1/tracks?album_id={ids['alb1_id']}", headers=_bearer(admin))
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert [t["track_number"] for t in data["items"]] == [1, 2]

    # Track detail
    res = await api_client.get(f"/api/v1/tracks/{ids['tr1_id']}", headers=_bearer(admin))
    assert res.status_code == 200
    track = res.json()
    assert track["title"] == "Airbag"
    assert track["disc_number"] == 1
    assert track["track_number"] == 1
    assert track["duration_seconds"] == 284.0
    assert track["container"] == "flac"
    assert track["replaygain_track_gain"] == -6.2
    assert track["stream_url"] == f"/api/v1/stream/audio/{ids['tr1_id']}"


@pytest.mark.asyncio
async def test_shelves_and_search(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, ids = await _seed_music_data(db_settings, db_sessionmaker)

    # Random shelf
    res = await api_client.get(
        "/api/v1/music/random?albums_limit=2&tracks_limit=3", headers=_bearer(admin)
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["albums"]) <= 2
    assert len(data["tracks"]) <= 3

    # Recently added
    res = await api_client.get("/api/v1/music/recently-added?limit=2", headers=_bearer(admin))
    assert res.status_code == 200
    assert len(res.json()) <= 2

    # Most played (no plays yet, should return empty list or 0 plays)
    res = await api_client.get("/api/v1/music/most-played", headers=_bearer(admin))
    assert res.status_code == 200

    # Music search
    res = await api_client.get("/api/v1/music/search?q=paranoid", headers=_bearer(admin))
    assert res.status_code == 200
    search_data = res.json()
    assert len(search_data["tracks"]) == 1
    assert search_data["tracks"][0]["title"] == "Paranoid Android"


@pytest.mark.asyncio
async def test_playlists_crud_and_reorder(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, ids = await _seed_music_data(db_settings, db_sessionmaker)

    # 1. Create playlist
    res = await api_client.post(
        "/api/v1/playlists",
        json={"name": "My Favorites", "description": "Best tunes"},
        headers=_bearer(admin),
    )
    assert res.status_code == 201, res.text
    p = res.json()
    p_id = p["id"]
    assert p["name"] == "My Favorites"
    assert p["description"] == "Best tunes"

    # 2. List playlists
    res = await api_client.get("/api/v1/playlists", headers=_bearer(admin))
    assert res.status_code == 200
    plist = res.json()
    assert len(plist) == 1
    assert plist[0]["item_count"] == 0

    # 3. Add items
    res = await api_client.post(
        f"/api/v1/playlists/{p_id}/items",
        json={"item_ids": [ids["tr1_id"], ids["tr3_id"]]},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    detail = res.json()
    assert len(detail["items"]) == 2
    assert detail["items"][0]["position"] == 0
    assert detail["items"][0]["track"]["id"] == ids["tr1_id"]
    assert detail["items"][1]["position"] == 1
    assert detail["items"][1]["track"]["id"] == ids["tr3_id"]
    pi1_id = detail["items"][0]["id"]
    pi2_id = detail["items"][1]["id"]

    # 4. Add third item
    res = await api_client.post(
        f"/api/v1/playlists/{p_id}/items",
        json={"item_ids": [ids["tr2_id"]]},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    detail = res.json()
    assert len(detail["items"]) == 3
    assert detail["items"][2]["position"] == 2
    pi3_id = detail["items"][2]["id"]

    # 5. Reorder items: move item 3 to position 0
    res = await api_client.put(
        f"/api/v1/playlists/{p_id}/items/reorder",
        json={"item_ids": [pi3_id, pi1_id, pi2_id]},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    reordered = res.json()
    assert [item["id"] for item in reordered["items"]] == [pi3_id, pi1_id, pi2_id]
    assert [item["position"] for item in reordered["items"]] == [0, 1, 2]

    # 6. Delete item
    res = await api_client.delete(
        f"/api/v1/playlists/{p_id}/items/{pi1_id}", headers=_bearer(admin)
    )
    assert res.status_code == 204
    res = await api_client.get(f"/api/v1/playlists/{p_id}", headers=_bearer(admin))
    assert res.status_code == 200
    remaining = res.json()
    assert len(remaining["items"]) == 2
    assert [item["id"] for item in remaining["items"]] == [pi3_id, pi2_id]
    assert [item["position"] for item in remaining["items"]] == [0, 1]

    # 7. Update playlist metadata
    res = await api_client.patch(
        f"/api/v1/playlists/{p_id}",
        json={"name": "Night Drive", "description": "Updated"},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    assert res.json()["name"] == "Night Drive"
    assert res.json()["description"] == "Updated"

    # 8. Delete playlist
    res = await api_client.delete(f"/api/v1/playlists/{p_id}", headers=_bearer(admin))
    assert res.status_code == 204

    # Verify 404
    res = await api_client.get(f"/api/v1/playlists/{p_id}", headers=_bearer(admin))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_playlist_user_isolation(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    lib_id, _ = await _seed_music_data(db_settings, db_sessionmaker)

    user1 = await _add_user(api_client, admin, "user1", [lib_id])
    user2 = await _add_user(api_client, admin, "user2", [lib_id])

    # User 1 creates playlist
    res = await api_client.post(
        "/api/v1/playlists",
        json={"name": "User 1 Jams"},
        headers=_bearer(user1),
    )
    assert res.status_code == 201
    p1_id = res.json()["id"]

    # User 2 cannot see it in list
    res = await api_client.get("/api/v1/playlists", headers=_bearer(user2))
    assert res.status_code == 200
    assert len(res.json()) == 0

    # User 2 cannot get detail
    res = await api_client.get(f"/api/v1/playlists/{p1_id}", headers=_bearer(user2))
    assert res.status_code == 404

    # User 2 cannot delete it
    res = await api_client.delete(f"/api/v1/playlists/{p1_id}", headers=_bearer(user2))
    assert res.status_code == 404
