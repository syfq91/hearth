"""Integration tests for unified full-text search across media types and people."""

import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Album,
    Book,
    Episode,
    ItemPerson,
    Library,
    LibraryAccess,
    MediaItem,
    Movie,
    Person,
    Season,
    Series,
    Track,
)
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}
VIEWER = {"username": "viewer", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    res = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert res.status_code == 201
    return res.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


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
    year: int = 2021,
    cast: tuple[str, ...] = (),
) -> str:
    async with sessionmaker() as session:
        item = MediaItem(
            library_id=library_id,
            type="movie",
            title=title,
            sort_title=sort_title(title),
            year=year,
            match_state="matched",
            overview=f"Synopsis of {title}",
            runtime_seconds=7200,
        )
        session.add(item)
        await session.flush()
        session.add(Movie(item_id=item.id))

        for idx, actor_name in enumerate(cast):
            person = Person(name=actor_name)
            session.add(person)
            await session.flush()
            session.add(
                ItemPerson(
                    item_id=item.id,
                    person_id=person.id,
                    role="cast",
                    ordering=idx,
                )
            )

        await session.commit()
        return item.id


async def _add_tv_show(
    sessionmaker: async_sessionmaker[AsyncSession],
    library_id: str,
    show_title: str,
    episode_title: str,
) -> tuple[str, str]:
    async with sessionmaker() as session:
        series_item = MediaItem(
            library_id=library_id,
            type="series",
            title=show_title,
            sort_title=sort_title(show_title),
            match_state="matched",
        )
        session.add(series_item)
        await session.flush()
        series = Series(item_id=series_item.id)
        session.add(series)
        await session.flush()

        season_item = MediaItem(
            library_id=library_id,
            type="season",
            title=f"{show_title} Season 1",
            sort_title=f"{show_title} Season 1",
            match_state="matched",
        )
        session.add(season_item)
        await session.flush()
        season = Season(item_id=season_item.id, series_id=series.item_id, season_number=1)
        session.add(season)
        await session.flush()

        ep_item = MediaItem(
            library_id=library_id,
            type="episode",
            title=episode_title,
            sort_title=sort_title(episode_title),
            match_state="matched",
        )
        session.add(ep_item)
        await session.flush()
        ep = Episode(
            item_id=ep_item.id,
            series_id=series.item_id,
            season_id=season.item_id,
            season_number=1,
            episode_number=1,
        )
        session.add(ep)
        await session.commit()
        return series_item.id, ep_item.id


@pytest.mark.asyncio
async def test_search_prefix_and_grouping(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    admin_tokens = await _setup(api_client)
    headers = _bearer(admin_tokens)

    movie_lib = await _make_library(db_sessionmaker, "Movies", "movie")
    tv_lib = await _make_library(db_sessionmaker, "TV", "tv")

    await _add_movie(
        db_sessionmaker, movie_lib, "Dune: Part One", 2021, cast=("Timothée Chalamet", "Zendaya")
    )
    await _add_movie(
        db_sessionmaker, movie_lib, "Dune: Part Two", 2024, cast=("Timothée Chalamet",)
    )
    await _add_tv_show(db_sessionmaker, tv_lib, "Dune: Prophecy", "The Hidden Sister")

    # 1. Search prefix "dun" matches movies, series and people
    res = await api_client.get("/api/v1/search?q=dun", headers=headers)
    assert res.status_code == 200, res.text
    data = res.json()
    assert len(data["movies"]) == 2
    assert len(data["series"]) == 1
    assert data["total"] >= 3

    # 2. Search specific type
    res_movies = await api_client.get("/api/v1/search?q=dun&type=movie", headers=headers)
    assert res_movies.status_code == 200
    movies_data = res_movies.json()
    assert len(movies_data["movies"]) == 2
    assert len(movies_data["series"]) == 0
    assert len(movies_data["people"]) == 0

    # 3. Search cast / person
    res_person = await api_client.get("/api/v1/search?q=chalamet", headers=headers)
    assert res_person.status_code == 200
    person_data = res_person.json()
    assert any("Chalamet" in p["name"] for p in person_data["people"])


@pytest.mark.asyncio
async def test_search_respects_library_access(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    admin_tokens = await _setup(api_client)

    lib_public = await _make_library(db_sessionmaker, "Public Library", "movie")
    lib_private = await _make_library(db_sessionmaker, "Secret Library", "movie")

    await _add_movie(db_sessionmaker, lib_public, "Public Star Wars Movie", 1977)
    await _add_movie(db_sessionmaker, lib_private, "Secret Star Wars Movie", 1980)

    # Create viewer user
    create_res = await api_client.post(
        "/api/v1/users",
        headers=_bearer(admin_tokens),
        json={"username": "viewer", "password": "correct horse", "role": "user"},
    )
    assert create_res.status_code == 201
    viewer_id = create_res.json()["id"]

    # Grant access ONLY to lib_public
    async with db_sessionmaker() as session:
        session.add(LibraryAccess(user_id=viewer_id, library_id=lib_public))
        await session.commit()

    # Login as viewer
    login_res = await api_client.post(
        "/api/v1/auth/login",
        json={"username": "viewer", "password": "correct horse"},
    )
    assert login_res.status_code == 200
    viewer_headers = _bearer(login_res.json())

    # Viewer search for "Star Wars" should ONLY return the public movie
    res = await api_client.get("/api/v1/search?q=Star Wars", headers=viewer_headers)
    assert res.status_code == 200
    data = res.json()
    movie_titles = [m["title"] for m in data["movies"]]
    assert "Public Star Wars Movie" in movie_titles
    assert "Secret Star Wars Movie" not in movie_titles


@pytest.mark.asyncio
async def test_search_empty_and_special_characters(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    admin_tokens = await _setup(api_client)
    headers = _bearer(admin_tokens)

    # Empty query or whitespace
    res = await api_client.get("/api/v1/search?q=   ", headers=headers)
    assert res.status_code == 200
    assert res.json()["total"] == 0

    # Special characters without words
    res = await api_client.get("/api/v1/search?q=***:::--", headers=headers)
    assert res.status_code == 200
    assert res.json()["total"] == 0


@pytest.mark.asyncio
async def test_search_music_and_books(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    admin_tokens = await _setup(api_client)
    headers = _bearer(admin_tokens)

    music_lib = await _make_library(db_sessionmaker, "Music", "music")
    book_lib = await _make_library(db_sessionmaker, "Books", "book")

    async with db_sessionmaker() as session:
        # Album
        album_item = MediaItem(
            library_id=music_lib,
            type="album",
            title="Random Access Memories",
            sort_title=sort_title("Random Access Memories"),
            match_state="matched",
        )
        session.add(album_item)
        await session.flush()
        session.add(Album(item_id=album_item.id))

        # Track
        track_item = MediaItem(
            library_id=music_lib,
            type="track",
            title="Get Lucky",
            sort_title=sort_title("Get Lucky"),
            match_state="matched",
        )
        session.add(track_item)
        await session.flush()
        session.add(Track(item_id=track_item.id, album_id=album_item.id))

        # Book
        book_item = MediaItem(
            library_id=book_lib,
            type="book",
            title="The Hobbit",
            sort_title=sort_title("The Hobbit"),
            match_state="matched",
        )
        session.add(book_item)
        await session.flush()
        session.add(Book(item_id=book_item.id))

        await session.commit()

    # Search for album
    res = await api_client.get("/api/v1/search?q=Random Access", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data["albums"]) == 1
    assert data["albums"][0]["title"] == "Random Access Memories"

    # Search for track
    res = await api_client.get("/api/v1/search?q=Get Lucky", headers=headers)
    assert res.status_code == 200
    assert len(res.json()["tracks"]) == 1

    # Search for book
    res = await api_client.get("/api/v1/search?q=Hobbit", headers=headers)
    assert res.status_code == 200
    assert len(res.json()["books"]) == 1
