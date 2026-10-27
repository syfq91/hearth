"""Integration tests for collections (admin-curated) and playlists (per-user)."""

import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Library,
    LibraryAccess,
    MediaItem,
    Movie,
    Track,
)
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}
VIEWER_A = {"username": "viewer_a", "password": "correct horse"}
VIEWER_B = {"username": "viewer_b", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    res = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert res.status_code == 201
    return res.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _login(client: httpx.AsyncClient, user: dict[str, str]) -> dict[str, str]:
    res = await client.post("/api/v1/auth/login", json=user)
    assert res.status_code == 200
    return res.json()


async def _make_library(
    sessionmaker: async_sessionmaker[AsyncSession], name: str, lib_type: str = "movie"
) -> str:
    async with sessionmaker() as session:
        lib = Library(name=f"{name} {uuid.uuid4().hex[:6]}", type=lib_type)
        session.add(lib)
        await session.commit()
        return lib.id


async def _add_movie(
    sessionmaker: async_sessionmaker[AsyncSession],
    library_id: str,
    title: str,
    tmdb_collection_id: int | None = None,
) -> str:
    async with sessionmaker() as session:
        item = MediaItem(
            library_id=library_id,
            type="movie",
            title=title,
            sort_title=sort_title(title),
            match_state="matched",
            runtime_seconds=7200,
            provider_ids={"tmdb_collection_id": tmdb_collection_id} if tmdb_collection_id else {},
        )
        session.add(item)
        await session.flush()
        session.add(Movie(item_id=item.id))
        await session.commit()
        return item.id


async def _add_track(
    sessionmaker: async_sessionmaker[AsyncSession],
    library_id: str,
    title: str,
    duration: int = 180,
) -> str:
    async with sessionmaker() as session:
        item = MediaItem(
            library_id=library_id,
            type="track",
            title=title,
            sort_title=sort_title(title),
            match_state="matched",
            runtime_seconds=duration,
        )
        session.add(item)
        await session.flush()
        session.add(Track(item_id=item.id, duration_seconds=float(duration)))
        await session.commit()
        return item.id


@pytest.mark.asyncio
async def test_collections_crud_and_tmdb_autocollect(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    admin_tokens = await _setup(api_client)
    admin_hdr = _bearer(admin_tokens)

    lib_id = await _make_library(db_sessionmaker, "Movies", "movie")
    m1_id = await _add_movie(db_sessionmaker, lib_id, "The Fellowship of the Ring", 119)
    m2_id = await _add_movie(db_sessionmaker, lib_id, "The Two Towers", 119)
    m3_id = await _add_movie(db_sessionmaker, lib_id, "The Return of the King", 119)

    # 1. Create a collection with TMDB collection ID auto-collection
    col_res = await api_client.post(
        "/api/v1/collections",
        headers=admin_hdr,
        json={
            "name": "The Lord of the Rings Collection",
            "overview": "Epic high-fantasy trilogy",
            "tmdb_id": 119,
        },
    )
    assert col_res.status_code == 201, col_res.text
    col_data = col_res.json()
    assert col_data["name"] == "The Lord of the Rings Collection"
    assert len(col_data["items"]) == 3
    col_id = col_data["id"]

    # 2. List collections
    list_res = await api_client.get("/api/v1/collections", headers=admin_hdr)
    assert list_res.status_code == 200
    summaries = list_res.json()
    assert len(summaries) == 1
    assert summaries[0]["item_count"] == 3

    # 3. Reorder items
    reorder_res = await api_client.put(
        f"/api/v1/collections/{col_id}/items/order",
        headers=admin_hdr,
        json={"item_ids": [m3_id, m2_id, m1_id]},
    )
    assert reorder_res.status_code == 200
    reordered_data = reorder_res.json()
    assert [i["id"] for i in reordered_data["items"]] == [m3_id, m2_id, m1_id]

    # 4. Remove an item
    rm_res = await api_client.delete(
        f"/api/v1/collections/{col_id}/items/{m2_id}",
        headers=admin_hdr,
    )
    assert rm_res.status_code == 200
    assert len(rm_res.json()["items"]) == 2

    # 5. Patch collection
    patch_res = await api_client.patch(
        f"/api/v1/collections/{col_id}",
        headers=admin_hdr,
        json={"name": "LOTR Collection Updated"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["name"] == "LOTR Collection Updated"

    # 6. Delete collection
    del_res = await api_client.delete(f"/api/v1/collections/{col_id}", headers=admin_hdr)
    assert del_res.status_code == 204

    # 7. Verify 404
    get_res = await api_client.get(f"/api/v1/collections/{col_id}", headers=admin_hdr)
    assert get_res.status_code == 404


@pytest.mark.asyncio
async def test_collections_permissions_and_library_filtering(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    admin_tokens = await _setup(api_client)
    admin_hdr = _bearer(admin_tokens)

    lib_public = await _make_library(db_sessionmaker, "Public", "movie")
    lib_private = await _make_library(db_sessionmaker, "Private", "movie")

    pub_movie = await _add_movie(db_sessionmaker, lib_public, "Public Movie")
    priv_movie = await _add_movie(db_sessionmaker, lib_private, "Private Movie")

    # Admin creates collection with both movies
    col_res = await api_client.post(
        "/api/v1/collections",
        headers=admin_hdr,
        json={"name": "Mixed Collection", "item_ids": [pub_movie, priv_movie]},
    )
    assert col_res.status_code == 201
    col_id = col_res.json()["id"]

    # Create viewer
    usr_res = await api_client.post(
        "/api/v1/users",
        headers=admin_hdr,
        json={"username": "viewer", "password": "correct horse", "role": "user"},
    )
    assert usr_res.status_code == 201
    viewer_id = usr_res.json()["id"]

    # Grant access ONLY to public library
    async with db_sessionmaker() as session:
        session.add(LibraryAccess(user_id=viewer_id, library_id=lib_public))
        await session.commit()

    viewer_tokens = await _login(api_client, {"username": "viewer", "password": "correct horse"})
    viewer_hdr = _bearer(viewer_tokens)

    # Viewer cannot create collection
    bad_post = await api_client.post(
        "/api/v1/collections",
        headers=viewer_hdr,
        json={"name": "Viewer Collection"},
    )
    assert bad_post.status_code == 403

    # Viewer viewing the mixed collection only sees the item from public library
    col_view = await api_client.get(f"/api/v1/collections/{col_id}", headers=viewer_hdr)
    assert col_view.status_code == 200
    v_items = col_view.json()["items"]
    assert len(v_items) == 1
    assert v_items[0]["id"] == pub_movie


@pytest.mark.asyncio
async def test_playlists_user_isolation_and_duration(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    admin_tokens = await _setup(api_client)
    admin_hdr = _bearer(admin_tokens)

    music_lib = await _make_library(db_sessionmaker, "Music", "music")
    t1 = await _add_track(db_sessionmaker, music_lib, "Track 1", 200)
    t2 = await _add_track(db_sessionmaker, music_lib, "Track 2", 300)

    # Create User A and User B
    for uname in ("user_a", "user_b"):
        res = await api_client.post(
            "/api/v1/users",
            headers=admin_hdr,
            json={"username": uname, "password": "correct horse", "role": "user"},
        )
        assert res.status_code == 201
        uid = res.json()["id"]
        async with db_sessionmaker() as session:
            session.add(LibraryAccess(user_id=uid, library_id=music_lib))
            await session.commit()

    tok_a = await _login(api_client, {"username": "user_a", "password": "correct horse"})
    tok_b = await _login(api_client, {"username": "user_b", "password": "correct horse"})
    hdr_a = _bearer(tok_a)
    hdr_b = _bearer(tok_b)

    # User A creates playlist
    create_res = await api_client.post(
        "/api/v1/playlists",
        headers=hdr_a,
        json={"name": "User A Jams", "description": "Good vibes", "item_ids": [t1, t2]},
    )
    assert create_res.status_code == 201
    pl_data = create_res.json()
    pl_id = pl_data["id"]
    assert pl_data["name"] == "User A Jams"
    assert pl_data["total_duration_seconds"] == 500.0
    assert pl_data["item_count"] == 2

    # User A lists playlists
    list_a = await api_client.get("/api/v1/playlists", headers=hdr_a)
    assert list_a.status_code == 200
    assert len(list_a.json()) == 1
    assert list_a.json()[0]["total_duration_seconds"] == 500.0

    # User B lists playlists -> 0 playlists
    list_b = await api_client.get("/api/v1/playlists", headers=hdr_b)
    assert list_b.status_code == 200
    assert len(list_b.json()) == 0

    # User B cannot access User A's playlist (403/404)
    get_b = await api_client.get(f"/api/v1/playlists/{pl_id}", headers=hdr_b)
    assert get_b.status_code in (403, 404)

    # User B cannot modify User A's playlist (403/404)
    mod_b = await api_client.patch(
        f"/api/v1/playlists/{pl_id}",
        headers=hdr_b,
        json={"name": "Hacked"},
    )
    assert mod_b.status_code in (403, 404)

    # User A reorders items
    reorder_res = await api_client.put(
        f"/api/v1/playlists/{pl_id}/items/order",
        headers=hdr_a,
        json={"item_ids": [t2, t1]},
    )
    assert reorder_res.status_code == 200
    assert [pi["item"]["id"] for pi in reorder_res.json()["items"]] == [t2, t1]

    # User A deletes playlist
    del_res = await api_client.delete(f"/api/v1/playlists/{pl_id}", headers=hdr_a)
    assert del_res.status_code == 204
