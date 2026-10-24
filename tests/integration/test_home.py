"""Integration tests for the home shelves."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Library, LibraryPath, MediaItem, Movie, User, UserItemState
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
    settings: Settings, sessionmaker: async_sessionmaker[AsyncSession], name: str
) -> str:
    root = settings.media_dir / name
    root.mkdir(exist_ok=True)
    async with sessionmaker() as session:
        library = Library(name=f"{name} {uuid.uuid4().hex[:8]}", type="movie")
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


async def test_home_shelves_require_authentication(api_client: httpx.AsyncClient) -> None:
    for path in ("continue-watching", "recently-added", "favorites"):
        response = await api_client.get(f"/api/v1/home/{path}")
        assert response.status_code == 401
