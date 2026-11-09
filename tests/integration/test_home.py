"""Integration tests for the home shelves."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Book,
    BookProgress,
    Library,
    LibraryPath,
    MediaItem,
    Movie,
    User,
    UserItemState,
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


async def _library(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    name: str,
    library_type: str = "movie",
) -> str:
    root = settings.media_dir / name
    root.mkdir(exist_ok=True)
    async with sessionmaker() as session:
        library = Library(name=f"{name} {uuid.uuid4().hex[:8]}", type=library_type)
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.commit()
        return library.id


async def _item(
    sessionmaker: async_sessionmaker[AsyncSession],
    library_id: str,
    *,
    title: str,
    added_at: datetime = NOW,
) -> str:
    async with sessionmaker() as session:
        item = MediaItem(
            library_id=library_id,
            type="movie",
            title=title,
            sort_title=sort_title(title),
            year=2000,
            added_at=added_at,
            runtime_seconds=6000,
            match_state="matched",
        )
        item.movie = Movie()
        session.add(item)
        await session.commit()
        return item.id


async def _book(
    sessionmaker: async_sessionmaker[AsyncSession],
    library_id: str,
    *,
    title: str,
    added_at: datetime = NOW,
) -> str:
    async with sessionmaker() as session:
        item = MediaItem(
            library_id=library_id,
            type="book",
            title=title,
            sort_title=sort_title(title),
            added_at=added_at,
            match_state="matched",
        )
        item.book = Book()
        session.add(item)
        await session.commit()
        return item.id


async def _book_progress(
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: str,
    book_id: str,
    *,
    percentage: float,
    completed: bool = False,
    updated_at: datetime = NOW,
) -> None:
    async with sessionmaker() as session:
        session.add(
            BookProgress(
                user_id=user_id,
                book_id=book_id,
                locator="epubcfi(/6/4!/4/2/10)",
                percentage=percentage,
                completed=completed,
                updated_at=updated_at,
            )
        )
        await session.commit()


async def _state(
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id: str,
    item_id: str,
    *,
    position_ms: int = 0,
    played: bool = False,
    favorite: bool = False,
    last_played_at: datetime | None = None,
    updated_at: datetime = NOW,
) -> None:
    async with sessionmaker() as session:
        session.add(
            UserItemState(
                user_id=user_id,
                item_id=item_id,
                position_ms=position_ms,
                played=played,
                favorite=favorite,
                rating=None,
                play_count=1 if position_ms else 0,
                last_played_at=last_played_at,
                updated_at=updated_at,
            )
        )
        await session.commit()


async def _user_id(sessionmaker: async_sessionmaker[AsyncSession], username: str) -> str:
    async with sessionmaker() as session:
        user = await session.scalar(select(User).where(User.username == username))
        assert user is not None
        return user.id


async def _titles(response: httpx.Response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["title"] for item in response.json()["items"]]


async def test_continue_watching_is_per_user_and_excludes_finished(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    library_a = await _library(db_settings, db_sessionmaker, "A")
    library_b = await _library(db_settings, db_sessionmaker, "B")
    alice = await _add_user(api_client, admin, "alice", [library_a])
    bob = await _add_user(api_client, admin, "bob", [library_a, library_b])
    alice_id = await _user_id(db_sessionmaker, "alice")
    bob_id = await _user_id(db_sessionmaker, "bob")

    newest = await _item(db_sessionmaker, library_a, title="Newest")
    older = await _item(db_sessionmaker, library_a, title="Older")
    finished = await _item(db_sessionmaker, library_a, title="Finished")
    unstarted = await _item(db_sessionmaker, library_a, title="Unstarted")
    elsewhere = await _item(db_sessionmaker, library_b, title="Elsewhere")

    await _state(
        db_sessionmaker,
        alice_id,
        newest,
        position_ms=2000,
        last_played_at=NOW - timedelta(minutes=1),
    )
    await _state(
        db_sessionmaker,
        alice_id,
        older,
        position_ms=1000,
        last_played_at=NOW - timedelta(minutes=10),
    )
    await _state(
        db_sessionmaker, alice_id, finished, position_ms=5900, played=True, last_played_at=NOW
    )
    await _state(db_sessionmaker, alice_id, unstarted, position_ms=0, last_played_at=NOW)
    await _state(
        db_sessionmaker,
        alice_id,
        elsewhere,
        position_ms=3000,
        last_played_at=NOW + timedelta(minutes=5),
    )
    await _state(
        db_sessionmaker,
        bob_id,
        newest,
        position_ms=5000,
        last_played_at=NOW + timedelta(minutes=10),
    )

    response = await api_client.get("/api/v1/home/continue-watching", headers=_bearer(alice))
    assert await _titles(response) == ["Newest", "Older"]
    payload = response.json()["items"]
    assert [item["watch_state"]["position_ms"] for item in payload] == [2000, 1000]

    admin_response = await api_client.get("/api/v1/home/continue-watching", headers=_bearer(admin))
    assert await _titles(admin_response) == []

    bob_response = await api_client.get("/api/v1/home/continue-watching", headers=_bearer(bob))
    assert await _titles(bob_response) == ["Newest"]
    assert bob_response.json()["items"][0]["watch_state"]["position_ms"] == 5000


async def test_continue_reading_is_per_user_and_excludes_finished(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    library_a = await _library(db_settings, db_sessionmaker, "A", library_type="book")
    library_b = await _library(db_settings, db_sessionmaker, "B", library_type="book")
    alice = await _add_user(api_client, admin, "alice", [library_a])
    bob = await _add_user(api_client, admin, "bob", [library_a, library_b])
    alice_id = await _user_id(db_sessionmaker, "alice")
    bob_id = await _user_id(db_sessionmaker, "bob")

    recent = await _book(db_sessionmaker, library_a, title="Recent")
    older = await _book(db_sessionmaker, library_a, title="Older")
    finished = await _book(db_sessionmaker, library_a, title="Finished")
    unread = await _book(db_sessionmaker, library_a, title="Unread")
    elsewhere = await _book(db_sessionmaker, library_b, title="Elsewhere")

    await _book_progress(
        db_sessionmaker, alice_id, recent, percentage=12.5, updated_at=NOW - timedelta(minutes=1)
    )
    await _book_progress(
        db_sessionmaker, alice_id, older, percentage=40.0, updated_at=NOW - timedelta(minutes=10)
    )
    await _book_progress(
        db_sessionmaker, alice_id, finished, percentage=100.0, completed=True, updated_at=NOW
    )
    await _book_progress(db_sessionmaker, alice_id, unread, percentage=0.0, updated_at=NOW)
    await _book_progress(
        db_sessionmaker, alice_id, elsewhere, percentage=30.0, updated_at=NOW + timedelta(minutes=5)
    )
    await _book_progress(db_sessionmaker, bob_id, recent, percentage=55.0)

    response = await api_client.get("/api/v1/home/continue-reading", headers=_bearer(alice))
    assert await _titles(response) == ["Recent", "Older"]
    assert [item["progress"]["percentage"] for item in response.json()["items"]] == [12.5, 40.0]
    assert response.json()["has_more"] is False

    limited = await api_client.get(
        "/api/v1/home/continue-reading", params={"limit": 1}, headers=_bearer(alice)
    )
    assert await _titles(limited) == ["Recent"]
    assert limited.json()["has_more"] is True

    bob_response = await api_client.get("/api/v1/home/continue-reading", headers=_bearer(bob))
    assert await _titles(bob_response) == ["Recent"]
    assert bob_response.json()["items"][0]["progress"]["percentage"] == 55.0

    admin_response = await api_client.get("/api/v1/home/continue-reading", headers=_bearer(admin))
    assert await _titles(admin_response) == []


async def test_recently_added_orders_and_filters_by_library(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    library_a = await _library(db_settings, db_sessionmaker, "A")
    library_b = await _library(db_settings, db_sessionmaker, "B")
    alice = await _add_user(api_client, admin, "alice", [library_a])

    await _item(db_sessionmaker, library_a, title="First", added_at=NOW - timedelta(days=2))
    await _item(db_sessionmaker, library_a, title="Second", added_at=NOW)
    await _item(db_sessionmaker, library_b, title="Hidden", added_at=NOW + timedelta(days=1))

    response = await api_client.get("/api/v1/home/recently-added", headers=_bearer(alice))
    assert await _titles(response) == ["Second", "First"]
    assert response.json()["has_more"] is False

    limited = await api_client.get(
        "/api/v1/home/recently-added", params={"limit": 1}, headers=_bearer(alice)
    )
    assert await _titles(limited) == ["Second"]
    assert limited.json()["has_more"] is True

    scoped = await api_client.get(
        "/api/v1/home/recently-added", params={"library_id": library_a}, headers=_bearer(alice)
    )
    assert await _titles(scoped) == ["Second", "First"]

    denied = await api_client.get(
        "/api/v1/home/recently-added", params={"library_id": library_b}, headers=_bearer(alice)
    )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "library_not_found"

    unknown = await api_client.get(
        "/api/v1/home/recently-added", params={"library_id": "missing"}, headers=_bearer(alice)
    )
    assert unknown.status_code == 404

    admin_response = await api_client.get("/api/v1/home/recently-added", headers=_bearer(admin))
    assert await _titles(admin_response) == ["Hidden", "Second", "First"]


async def test_recently_added_groups_episodes_by_series(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from app.db.models import Episode, Season, Series

    admin = await _setup(api_client)
    tv_lib = await _library(db_settings, db_sessionmaker, "TV", library_type="tv")

    async with db_sessionmaker() as session:
        series_item = MediaItem(
            library_id=tv_lib,
            type="series",
            title="Great Show",
            sort_title="great show",
            year=2020,
            added_at=NOW - timedelta(days=5),
            runtime_seconds=0,
            match_state="matched",
        )
        series_item.series = Series()
        session.add(series_item)
        await session.flush()

        season_item = MediaItem(
            library_id=tv_lib,
            type="season",
            title="Season 1",
            sort_title="season 1",
            year=2020,
            added_at=NOW - timedelta(days=5),
            runtime_seconds=0,
            match_state="matched",
        )
        season_item.season = Season(series_id=series_item.id, season_number=1)
        session.add(season_item)
        await session.flush()

        for ep_num in [1, 2, 3]:
            ep_item = MediaItem(
                library_id=tv_lib,
                type="episode",
                title=f"Episode {ep_num}",
                sort_title=f"episode {ep_num}",
                year=2020,
                added_at=NOW - timedelta(minutes=10 - ep_num),
                runtime_seconds=1800,
                match_state="matched",
            )
            ep_item.episode = Episode(
                series_id=series_item.id,
                season_id=season_item.id,
                season_number=1,
                episode_number=ep_num,
            )
            session.add(ep_item)
        await session.commit()

    res = await api_client.get("/api/v1/home/recently-added", headers=_bearer(admin))
    assert res.status_code == 200
    items = res.json()["items"]
    # 3 episodes should be grouped into a single card for the series
    assert len(items) == 1
    assert items[0]["title"] == "Great Show"
    assert items[0]["type"] == "series"
    assert items[0]["episode_count"] == 3


async def test_favorites_are_per_user_and_ordered_by_recent_change(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    library_a = await _library(db_settings, db_sessionmaker, "A")
    alice = await _add_user(api_client, admin, "alice", [library_a])
    bob = await _add_user(api_client, admin, "bob", [library_a])
    alice_id = await _user_id(db_sessionmaker, "alice")
    bob_id = await _user_id(db_sessionmaker, "bob")

    first = await _item(db_sessionmaker, library_a, title="First")
    second = await _item(db_sessionmaker, library_a, title="Second")
    unfavorited = await _item(db_sessionmaker, library_a, title="Unfavorited")

    await _state(
        db_sessionmaker, alice_id, first, favorite=True, updated_at=NOW - timedelta(hours=1)
    )
    await _state(db_sessionmaker, alice_id, second, favorite=True, updated_at=NOW)
    await _state(db_sessionmaker, alice_id, unfavorited, favorite=False)
    await _state(db_sessionmaker, bob_id, first, favorite=True)

    response = await api_client.get("/api/v1/home/favorites", headers=_bearer(alice))
    assert await _titles(response) == ["Second", "First"]
    assert all(item["watch_state"]["favorite"] for item in response.json()["items"])

    removed = await api_client.post(
        f"/api/v1/items/{second}/favorite",
        json={"favorite": False},
        headers=_bearer(alice),
    )
    assert removed.status_code == 200
    updated = await api_client.get("/api/v1/home/favorites", headers=_bearer(alice))
    assert await _titles(updated) == ["First"]

    bob_response = await api_client.get("/api/v1/home/favorites", headers=_bearer(bob))
    assert await _titles(bob_response) == ["First"]


async def test_home_overview_returns_composite_shelves(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    library_a = await _library(db_settings, db_sessionmaker, "Movies", library_type="movie")
    alice = await _add_user(api_client, admin, "alice", [library_a])
    alice_id = await _user_id(db_sessionmaker, "alice")

    item1 = await _item(db_sessionmaker, library_a, title="Movie 1", added_at=NOW)
    await _state(db_sessionmaker, alice_id, item1, position_ms=1000, last_played_at=NOW)

    response = await api_client.get("/api/v1/home", headers=_bearer(alice))
    assert response.status_code == 200
    data = response.json()
    assert "continue_watching" in data
    assert "continue_reading" in data
    assert "next_up" in data
    assert "recent_libraries" in data
    assert "recent_albums" in data
    assert len(data["continue_watching"]["items"]) == 1
    assert data["continue_watching"]["items"][0]["title"] == "Movie 1"
    assert len(data["recent_libraries"]) == 1
    assert data["recent_libraries"][0]["library"]["name"].startswith("Movies")
    assert len(data["recent_libraries"][0]["shelf"]["items"]) == 1


async def test_home_shelves_require_authentication(api_client: httpx.AsyncClient) -> None:
    for path in ("", "continue-watching", "continue-reading", "recently-added", "favorites"):
        response = await api_client.get(f"/api/v1/home/{path}".rstrip("/"))
        assert response.status_code == 401
