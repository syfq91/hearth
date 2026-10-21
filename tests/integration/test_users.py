"""Integration tests for administrator user management."""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Library, LibraryAccess

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _create_user(
    client: httpx.AsyncClient, admin: dict[str, str], **overrides: object
) -> httpx.Response:
    payload: dict[str, object] = {"username": "viewer", "password": "viewerpass"}
    payload.update(overrides)
    return await client.post("/api/v1/users", json=payload, headers=_bearer(admin))


async def test_admin_creates_user_with_options_and_grants(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    async with db_sessionmaker() as session:
        library = Library(name="Movies", type="movie")
        session.add(library)
        await session.commit()
        library_id = library.id

    response = await _create_user(
        api_client,
        admin,
        max_bitrate=8_000_000,
        allow_transcode=False,
        max_rating="PG-13",
        library_ids=[library_id],
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["role"] == "user"
    assert body["max_bitrate"] == 8_000_000
    assert body["allow_transcode"] is False
    assert body["max_rating"] == "PG-13"
    assert body["library_ids"] == [library_id]

    listed = await api_client.get("/api/v1/users", headers=_bearer(admin))
    assert {item["username"] for item in listed.json()} == {"admin", "viewer"}


async def test_non_admin_cannot_manage_users(api_client: httpx.AsyncClient) -> None:
    admin = await _setup(api_client)
    await _create_user(api_client, admin)
    login = await api_client.post(
        "/api/v1/auth/login", json={"username": "viewer", "password": "viewerpass"}
    )

    response = await api_client.get("/api/v1/users", headers=_bearer(login.json()))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "admin_required"


async def test_duplicate_username_is_rejected(api_client: httpx.AsyncClient) -> None:
    admin = await _setup(api_client)
    await _create_user(api_client, admin)

    response = await _create_user(api_client, admin)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "username_taken"


async def test_enabled_user_limit_is_enforced(
    api_client: httpx.AsyncClient, db_settings: Settings
) -> None:
    db_settings.max_enabled_users = 2
    admin = await _setup(api_client)
    assert (await _create_user(api_client, admin)).status_code == 201

    response = await _create_user(api_client, admin, username="third", password="thirdpass")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "user_limit_reached"


async def test_disabling_user_invalidates_tokens_immediately(
    api_client: httpx.AsyncClient,
) -> None:
    admin = await _setup(api_client)
    created = (await _create_user(api_client, admin)).json()
    login = await api_client.post(
        "/api/v1/auth/login", json={"username": "viewer", "password": "viewerpass"}
    )
    viewer = login.json()
    assert (await api_client.get("/api/v1/auth/me", headers=_bearer(viewer))).status_code == 200

    disabled = await api_client.patch(
        f"/api/v1/users/{created['id']}",
        json={"disabled": True},
        headers=_bearer(admin),
    )
    assert disabled.status_code == 200

    assert (await api_client.get("/api/v1/auth/me", headers=_bearer(viewer))).status_code == 401
    relogin = await api_client.post(
        "/api/v1/auth/login", json={"username": "viewer", "password": "viewerpass"}
    )
    assert relogin.status_code == 403

    # Re-enabling must not resurrect the old access token.
    await api_client.patch(
        f"/api/v1/users/{created['id']}",
        json={"disabled": False},
        headers=_bearer(admin),
    )
    assert (await api_client.get("/api/v1/auth/me", headers=_bearer(viewer))).status_code == 401


async def test_password_reset_replaces_password_and_revokes_sessions(
    api_client: httpx.AsyncClient,
) -> None:
    admin = await _setup(api_client)
    created = (await _create_user(api_client, admin)).json()
    login = await api_client.post(
        "/api/v1/auth/login", json={"username": "viewer", "password": "viewerpass"}
    )
    old = login.json()

    response = await api_client.post(
        f"/api/v1/users/{created['id']}/password",
        json={"password": "brand new password"},
        headers=_bearer(admin),
    )
    assert response.status_code == 200

    assert (
        await api_client.post(
            "/api/v1/auth/login", json={"username": "viewer", "password": "viewerpass"}
        )
    ).status_code == 401
    assert (
        await api_client.post(
            "/api/v1/auth/login", json={"username": "viewer", "password": "brand new password"}
        )
    ).status_code == 200
    refresh = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old["refresh_token"]}
    )
    assert refresh.status_code == 401


async def test_admin_cannot_disable_self(api_client: httpx.AsyncClient) -> None:
    admin = await _setup(api_client)
    me = (await api_client.get("/api/v1/auth/me", headers=_bearer(admin))).json()

    response = await api_client.patch(
        f"/api/v1/users/{me['id']}",
        json={"disabled": True},
        headers=_bearer(admin),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "cannot_modify_self"


async def test_unknown_library_grant_is_rejected(
    api_client: httpx.AsyncClient,
) -> None:
    admin = await _setup(api_client)

    response = await _create_user(api_client, admin, library_ids=["missing"])

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "library_not_found"


async def test_updating_grants_replaces_them(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    async with db_sessionmaker() as session:
        first = Library(name="First", type="movie")
        second = Library(name="Second", type="tv")
        session.add_all([first, second])
        await session.commit()
        first_id, second_id = first.id, second.id

    created = (await _create_user(api_client, admin, library_ids=[first_id])).json()
    response = await api_client.patch(
        f"/api/v1/users/{created['id']}",
        json={"library_ids": [second_id]},
        headers=_bearer(admin),
    )

    assert response.status_code == 200
    assert response.json()["library_ids"] == [second_id]
    async with db_sessionmaker() as session:
        grants = (
            await session.scalars(
                select(LibraryAccess).where(LibraryAccess.user_id == created["id"])
            )
        ).all()
    assert len(grants) == 1


@pytest.mark.parametrize("path", ["/api/v1/users", "/api/v1/users/missing"])
async def test_user_management_requires_authentication(
    api_client: httpx.AsyncClient, path: str
) -> None:
    assert (await api_client.get(path)).status_code == 401
