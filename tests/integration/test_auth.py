"""Integration tests for setup, login, refresh rotation, logout and me."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.secrets import load_keys
from app.core.tokens import create_access_token, verify_media_token
from app.db.models import RefreshToken, User

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


async def _login(client: httpx.AsyncClient, password: str = ADMIN["password"]) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_setup_creates_admin_and_returns_tokens(api_client: httpx.AsyncClient) -> None:
    tokens = await _setup(api_client)

    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 15 * 60
    assert tokens["refresh_token"]
    me = await api_client.get("/api/v1/auth/me", headers=_bearer(tokens))
    assert me.status_code == 200
    assert me.json()["username"] == "admin"
    assert me.json()["role"] == "admin"
    assert me.json()["disabled"] is False


async def test_setup_is_rejected_once_an_admin_exists(api_client: httpx.AsyncClient) -> None:
    await _setup(api_client)

    response = await api_client.post("/api/v1/auth/setup", json=ADMIN)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "setup_complete"


async def test_setup_enforces_password_policy(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        "/api/v1/auth/setup", json={"username": "admin", "password": "short"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "weak_password"


async def test_login_accepts_valid_credentials_and_normalizes_username(
    api_client: httpx.AsyncClient,
) -> None:
    await _setup(api_client)

    response = await api_client.post(
        "/api/v1/auth/login", json={"username": "  ADMIN ", "password": ADMIN["password"]}
    )

    assert response.status_code == 200


async def test_login_rejects_bad_password_and_unknown_user(api_client: httpx.AsyncClient) -> None:
    await _setup(api_client)

    bad = await api_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong password"}
    )
    unknown = await api_client.post(
        "/api/v1/auth/login", json={"username": "nobody", "password": "wrong password"}
    )

    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "invalid_credentials"
    assert unknown.status_code == 401
    assert unknown.json()["error"]["code"] == "invalid_credentials"


async def test_me_requires_a_bearer_token(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_disabled_user_token_stops_working_immediately(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    tokens = await _setup(api_client)
    assert (await api_client.get("/api/v1/auth/me", headers=_bearer(tokens))).status_code == 200

    async with db_sessionmaker() as session:
        await session.execute(update(User).values(disabled=True))
        await session.commit()

    response = await api_client.get("/api/v1/auth/me", headers=_bearer(tokens))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "account_disabled"


async def test_token_version_bump_revokes_access_token(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    tokens = await _setup(api_client)

    async with db_sessionmaker() as session:
        await session.execute(update(User).values(token_version=1))
        await session.commit()

    response = await api_client.get("/api/v1/auth/me", headers=_bearer(tokens))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_revoked"


async def test_expired_access_token_is_rejected(
    api_client: httpx.AsyncClient, db_settings: Settings
) -> None:
    await _setup(api_client)
    keys = load_keys(db_settings)
    expired, _ = create_access_token(
        user_id="someone",
        role="admin",
        token_version=0,
        settings=db_settings,
        key=keys.jwt,
        now=datetime.now(UTC) - timedelta(minutes=16),
    )

    response = await api_client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


async def test_refresh_rotates_and_reuse_revokes_the_family(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    tokens = await _setup(api_client)

    first = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert first.status_code == 200
    rotated = first.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]

    # The old token was already rotated: reuse is treated as theft.
    replay = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "refresh_token_reuse"

    # The whole family, including the freshly rotated token, is now dead.
    after = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
    )
    assert after.status_code == 401

    async with db_sessionmaker() as session:
        rows = (await session.scalars(select(RefreshToken))).all()
    assert rows
    assert all(row.revoked_at is not None for row in rows)
    assert all(row.token_hash != tokens["refresh_token"] for row in rows)
    assert all(len(row.token_hash) == 64 for row in rows)


async def test_logout_revokes_refresh_family(api_client: httpx.AsyncClient) -> None:
    tokens = await _setup(api_client)

    logout = await api_client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=_bearer(tokens),
    )
    assert logout.status_code == 204

    refresh = await api_client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 401


async def test_login_of_disabled_user_is_forbidden(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    await _setup(api_client)
    async with db_sessionmaker() as session:
        await session.execute(update(User).values(disabled=True))
        await session.commit()

    response = await api_client.post("/api/v1/auth/login", json=ADMIN)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "account_disabled"


async def test_media_token_endpoint_requires_login(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post("/api/v1/auth/media-tokens", json={})

    assert response.status_code == 401


async def test_media_token_is_scoped_and_verifiable(
    api_client: httpx.AsyncClient, db_settings: Settings
) -> None:
    tokens = await _setup(api_client)

    response = await api_client.post(
        "/api/v1/auth/media-tokens",
        json={"item_id": "item-123"},
        headers=_bearer(tokens),
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["expires_in"] == 6 * 60 * 60
    assert payload["item_id"] == "item-123"
    claims = verify_media_token(payload["token"], key=load_keys(db_settings).media)
    assert claims.item_id == "item-123"


async def test_media_token_stops_working_when_user_is_disabled(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    from app.core.deps import get_media_user
    from app.core.errors import UnauthorizedError
    from app.core.tokens import verify_media_token

    tokens = await _setup(api_client)
    minted = await api_client.post("/api/v1/auth/media-tokens", json={}, headers=_bearer(tokens))
    claims = verify_media_token(minted.json()["token"], key=load_keys(db_settings).media)

    async with db_sessionmaker() as session:
        await session.execute(update(User).values(disabled=True))
        await session.commit()
        with pytest.raises(UnauthorizedError) as excinfo:
            await get_media_user(claims, session)

    assert excinfo.value.code == "account_disabled"
