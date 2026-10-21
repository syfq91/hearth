"""Tests for roles and per-library visibility (404 rather than 403)."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.deps import require_admin, require_user
from app.core.errors import ForbiddenError
from app.core.passwords import hash_password
from app.db.models import Library, LibraryAccess, User

ADMIN = {"username": "admin", "password": "correct horse"}


async def _bootstrap(
    client: httpx.AsyncClient,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Create an admin via setup, plus a normal user and two libraries."""
    admin_login = await client.post("/api/v1/auth/setup", json=ADMIN)
    admin_tokens = admin_login.json()

    async with sessionmaker() as session:
        user = User(username="viewer", password_hash=hash_password("viewerpass"))
        granted = Library(name="Movies", type="movie")
        hidden = Library(name="Music", type="music")
        session.add_all([user, granted, hidden])
        await session.flush()
        session.add(LibraryAccess(library_id=granted.id, user_id=user.id))
        await session.commit()
        ids = {"granted": granted.id, "hidden": hidden.id}

    login = await client.post(
        "/api/v1/auth/login", json={"username": "viewer", "password": "viewerpass"}
    )
    return admin_tokens, login.json(), ids


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_libraries_require_authentication(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get("/api/v1/libraries")).status_code == 401


async def test_admin_sees_all_libraries_and_user_sees_grants(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin, user, _ids = await _bootstrap(api_client, db_sessionmaker)

    admin_list = await api_client.get("/api/v1/libraries", headers=_bearer(admin))
    user_list = await api_client.get("/api/v1/libraries", headers=_bearer(user))

    assert {item["name"] for item in admin_list.json()} == {"Movies", "Music"}
    assert [item["name"] for item in user_list.json()] == ["Movies"]


async def test_granted_library_is_returned(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    _admin, user, ids = await _bootstrap(api_client, db_sessionmaker)

    response = await api_client.get(f"/api/v1/libraries/{ids['granted']}", headers=_bearer(user))

    assert response.status_code == 200
    assert response.json()["name"] == "Movies"


async def test_ungranted_library_returns_404_not_403(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    _admin, user, ids = await _bootstrap(api_client, db_sessionmaker)

    response = await api_client.get(f"/api/v1/libraries/{ids['hidden']}", headers=_bearer(user))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "library_not_found"

    missing = await api_client.get("/api/v1/libraries/does-not-exist", headers=_bearer(user))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "library_not_found"


async def test_admin_can_access_any_library(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin, _user, ids = await _bootstrap(api_client, db_sessionmaker)

    response = await api_client.get(f"/api/v1/libraries/{ids['hidden']}", headers=_bearer(admin))

    assert response.status_code == 200


async def test_require_admin_rejects_normal_users(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        session.add_all(
            [
                User(username="plain", password_hash="x", role="user"),
                User(username="boss", password_hash="x", role="admin"),
            ]
        )
        await session.commit()
        user = await session.scalar(select(User).where(User.username == "plain"))
        admin = await session.scalar(select(User).where(User.username == "boss"))

    assert user is not None and admin is not None
    assert await require_user(user) is user
    with pytest.raises(ForbiddenError):
        await require_admin(user)
    assert await require_admin(admin) is admin
