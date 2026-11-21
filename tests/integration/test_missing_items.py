"""Regression tests for hiding soft-deleted media from users."""

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.base import utcnow
from app.db.models import (
    Episode,
    Library,
    MediaFile,
    MediaItem,
    Movie,
    Series,
)
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    res = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert res.status_code == 201
    return res.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _file(path: str, missing: bool = False) -> MediaFile:
    return MediaFile(
        path=path,
        size_bytes=1024,
        mtime=1.0,
        partial_hash="a" * 64,
        missing_since=utcnow() if missing else None,
    )


@pytest.mark.asyncio
async def test_fully_missing_items_hidden_from_users_but_visible_to_admin(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    admin = _bearer(await _setup(api_client))

    async with db_sessionmaker() as session:
        lib = Library(name="Movies", type="movie")
        session.add(lib)
        await session.flush()

        partial = MediaItem(
            library_id=lib.id,
            type="movie",
            title="Partially Present",
            sort_title=sort_title("Partially Present"),
            match_state="matched",
        )
        partial.files.append(_file("/media/partial-a.mkv"))
        partial.files.append(_file("/media/partial-b.mkv", missing=True))
        session.add(partial)
        await session.flush()
        session.add(Movie(item_id=partial.id))

        gone = MediaItem(
            library_id=lib.id,
            type="movie",
            title="Entirely Gone",
            sort_title=sort_title("Entirely Gone"),
            match_state="matched",
        )
        gone.files.append(_file("/media/gone.mkv", missing=True))
        session.add(gone)
        await session.flush()
        session.add(Movie(item_id=gone.id))

        library_id = lib.id
        partial_id = partial.id
        gone_id = gone.id
        await session.commit()

    listing = await api_client.get(f"/api/v1/libraries/{library_id}/items", headers=admin)
    assert listing.status_code == 200
    titles = {item["title"] for item in listing.json()["items"]}
    assert "Partially Present" in titles
    assert "Entirely Gone" not in titles

    assert (await api_client.get(f"/api/v1/items/{partial_id}", headers=admin)).status_code == 200
    assert (await api_client.get(f"/api/v1/items/{gone_id}", headers=admin)).status_code == 404

    results = await api_client.get("/api/v1/search?q=Entirely", headers=admin)
    assert results.status_code == 200
    assert all(item["title"] != "Entirely Gone" for item in results.json()["movies"])

    missing = await api_client.get("/api/v1/admin/missing-files", headers=admin)
    assert missing.status_code == 200
    page = missing.json()
    # Includes the missing version of the partially-present item as well.
    assert page["total"] == 2
    paths = {entry["path"] for entry in page["items"]}
    assert paths == {"/media/gone.mkv", "/media/partial-b.mkv"}
    gone_entry = next(entry for entry in page["items"] if entry["path"] == "/media/gone.mkv")
    assert gone_entry["item_title"] == "Entirely Gone"
    assert gone_entry["library_id"] == library_id


@pytest.mark.asyncio
async def test_missing_series_hidden_when_all_episode_files_are_missing(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    admin = _bearer(await _setup(api_client))

    async with db_sessionmaker() as session:
        lib = Library(name="TV", type="tv")
        session.add(lib)
        await session.flush()

        series_item = MediaItem(
            library_id=lib.id,
            type="series",
            title="Vanished Show",
            sort_title=sort_title("Vanished Show"),
            match_state="matched",
        )
        series_item.series = Series()
        session.add(series_item)
        await session.flush()

        episode_item = MediaItem(
            library_id=lib.id,
            type="episode",
            title="S01E01",
            sort_title="s01e01",
            match_state="matched",
        )
        file_row = _file("/media/vanished-s01e01.mkv", missing=True)
        episode_item.files.append(file_row)
        session.add(episode_item)
        await session.flush()
        session.add(
            Episode(
                item_id=episode_item.id,
                series_id=series_item.id,
                file_id=file_row.id,
                season_number=1,
                episode_number=1,
            )
        )
        await session.flush()

        library_id = lib.id
        series_id = series_item.id
        await session.commit()

    listing = await api_client.get(f"/api/v1/libraries/{library_id}/items", headers=admin)
    assert listing.status_code == 200
    assert all(item["title"] != "Vanished Show" for item in listing.json()["items"])
    assert (await api_client.get(f"/api/v1/items/{series_id}", headers=admin)).status_code == 404


@pytest.mark.asyncio
async def test_missing_files_endpoint_requires_admin(
    api_client: httpx.AsyncClient,
) -> None:
    admin = _bearer(await _setup(api_client))
    created = await api_client.post(
        "/api/v1/users",
        headers=admin,
        json={"username": "viewer", "password": "correct horse", "role": "user"},
    )
    assert created.status_code == 201
    login = await api_client.post(
        "/api/v1/auth/login", json={"username": "viewer", "password": "correct horse"}
    )
    viewer = _bearer(login.json())

    res = await api_client.get("/api/v1/admin/missing-files", headers=viewer)
    assert res.status_code == 403
