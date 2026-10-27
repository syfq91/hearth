"""Integration tests for per-user content rating restrictions and access controls."""

import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Library,
    LibraryAccess,
    LibraryPath,
    MediaFile,
    MediaItem,
    Movie,
)
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    res = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert res.status_code == 201
    return res.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _login(client: httpx.AsyncClient, username: str) -> dict[str, str]:
    res = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct horse"},
    )
    assert res.status_code == 200
    return res.json()


async def _make_library(
    sessionmaker: async_sessionmaker[AsyncSession],
    media_dir: Path,
    name: str = "Movies",
) -> tuple[str, Path]:
    root = media_dir / f"lib_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    async with sessionmaker() as session:
        lib = Library(name=name, type="movie")
        lib.paths.append(LibraryPath(path=str(root)))
        session.add(lib)
        await session.commit()
        return lib.id, root


async def _add_movie(
    sessionmaker: async_sessionmaker[AsyncSession],
    library_id: str,
    root: Path,
    title: str,
    content_rating: str | None,
) -> tuple[str, str]:
    file_path = root / f"{title.replace(' ', '_')}.mp4"
    content = b"VIDEO_CONTENT_RATED" * 10
    file_path.write_bytes(content)

    async with sessionmaker() as session:
        item = MediaItem(
            library_id=library_id,
            type="movie",
            title=title,
            sort_title=sort_title(title),
            content_rating=content_rating,
            match_state="matched",
            runtime_seconds=7200,
        )
        session.add(item)
        await session.flush()
        session.add(Movie(item_id=item.id))

        media_file = MediaFile(
            item_id=item.id,
            path=str(file_path),
            size_bytes=len(content),
            mtime=file_path.stat().st_mtime,
            partial_hash=uuid.uuid4().hex * 2,
            container="mp4",
            duration_seconds=7200.0,
        )
        session.add(media_file)
        await session.commit()
        return item.id, media_file.id


@pytest.mark.asyncio
async def test_content_rating_filtering_across_endpoints(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin_tokens = await _setup(api_client)
    admin_hdr = _bearer(admin_tokens)

    lib_id, root = await _make_library(db_sessionmaker, db_settings.media_dir)

    # Seed 3 movies: G, PG-13, R
    g_id, g_fid = await _add_movie(db_sessionmaker, lib_id, root, "Finding Nemo", "G")
    pg13_id, pg13_fid = await _add_movie(db_sessionmaker, lib_id, root, "The Avengers", "PG-13")
    r_id, r_fid = await _add_movie(db_sessionmaker, lib_id, root, "Deadpool", "R")

    # Create restricted user with max_rating = "PG" (tier 2)
    user_res = await api_client.post(
        "/api/v1/users",
        headers=admin_hdr,
        json={
            "username": "kid",
            "password": "correct horse",
            "role": "user",
            "max_rating": "PG",
        },
    )
    assert user_res.status_code == 201
    kid_id = user_res.json()["id"]

    # Grant library access to the kid
    async with db_sessionmaker() as session:
        session.add(LibraryAccess(user_id=kid_id, library_id=lib_id))
        await session.commit()

    kid_tokens = await _login(api_client, "kid")
    kid_hdr = _bearer(kid_tokens)

    # 1. Library items browse: Kid should ONLY see Finding Nemo (G)
    browse_res = await api_client.get(f"/api/v1/libraries/{lib_id}/items", headers=kid_hdr)
    assert browse_res.status_code == 200
    browse_items = browse_res.json()["items"]
    assert len(browse_items) == 1
    assert browse_items[0]["id"] == g_id
    assert browse_items[0]["content_rating"] == "G"

    # Admin sees all 3 movies
    admin_browse = await api_client.get(f"/api/v1/libraries/{lib_id}/items", headers=admin_hdr)
    assert admin_browse.status_code == 200
    assert len(admin_browse.json()["items"]) == 3

    # 2. Search endpoint: Kid searches for "Deadpool" -> 0 results
    search_deadpool = await api_client.get("/api/v1/search?q=Deadpool", headers=kid_hdr)
    assert search_deadpool.status_code == 200
    assert len(search_deadpool.json()["movies"]) == 0

    # Kid searches for "Finding Nemo" -> 1 result
    search_nemo = await api_client.get("/api/v1/search?q=Nemo", headers=kid_hdr)
    assert search_nemo.status_code == 200
    assert len(search_nemo.json()["movies"]) == 1

    # 3. Item detail: Kid requesting R-rated movie gets 404
    detail_r = await api_client.get(f"/api/v1/items/{r_id}", headers=kid_hdr)
    assert detail_r.status_code == 404

    # Kid requesting G-rated movie gets 200
    detail_g = await api_client.get(f"/api/v1/items/{g_id}", headers=kid_hdr)
    assert detail_g.status_code == 200
    assert detail_g.json()["content_rating"] == "G"

    # 4. Playback-info: Kid requesting R-rated movie gets 404
    pb_r = await api_client.post(f"/api/v1/items/{r_id}/playback-info", headers=kid_hdr)
    assert pb_r.status_code == 404

    # 5. Direct Stream: Kid requesting R-rated file gets 404
    stream_r = await api_client.get(f"/api/v1/stream/{r_fid}", headers=kid_hdr)
    assert stream_r.status_code == 404

    # Kid streaming G-rated file gets 200
    stream_g = await api_client.get(f"/api/v1/stream/{g_fid}", headers=kid_hdr)
    assert stream_g.status_code == 200
