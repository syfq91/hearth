"""Integration tests for login throttling and the admin audit log."""

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_login_is_throttled_per_ip(api_client: httpx.AsyncClient) -> None:
    await _setup(api_client)

    for _ in range(5):
        response = await api_client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong password"}
        )
        assert response.status_code == 401

    blocked = await api_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong password"}
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1

    # Even the correct password is refused while the window is saturated.
    correct = await api_client.post("/api/v1/auth/login", json=ADMIN)
    assert correct.status_code == 429


async def test_successful_login_resets_username_counter(
    api_client: httpx.AsyncClient, db_settings: Settings
) -> None:
    db_settings.login_rate_limit_attempts = 2
    await _setup(api_client)

    for _ in range(1):
        await api_client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong password"}
        )
    assert (await api_client.post("/api/v1/auth/login", json=ADMIN)).status_code == 200

    # The username counter was cleared, so one more failure is still allowed.
    failed = await api_client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong password"}
    )
    assert failed.status_code == 401


async def test_audit_log_records_admin_actions_and_is_admin_only(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    from app.core.passwords import hash_password
    from app.db.models import User

    admin = await _setup(api_client)
    created = await api_client.post(
        "/api/v1/users",
        json={"username": "viewer", "password": "viewerpass"},
        headers=_bearer(admin),
    )
    assert created.status_code == 201

    response = await api_client.get("/api/v1/admin/audit", headers=_bearer(admin))

    assert response.status_code == 200
    actions = [entry["action"] for entry in response.json()]
    assert "auth.setup" in actions
    assert "user.create" in actions
    assert all("password" not in entry["details"] for entry in response.json())

    async with db_sessionmaker() as session:
        session.add(User(username="plain", password_hash=hash_password("plainpass")))
        await session.commit()
    login = await api_client.post(
        "/api/v1/auth/login", json={"username": "plain", "password": "plainpass"}
    )
    forbidden = await api_client.get("/api/v1/admin/audit", headers=_bearer(login.json()))
    assert forbidden.status_code == 403


async def test_audit_log_can_be_filtered_and_limited(api_client: httpx.AsyncClient) -> None:
    admin = await _setup(api_client)
    for name in ("one", "two", "three"):
        await api_client.post(
            "/api/v1/users",
            json={"username": name, "password": "password123"},
            headers=_bearer(admin),
        )

    all_entries = await api_client.get(
        "/api/v1/admin/audit", params={"limit": 2}, headers=_bearer(admin)
    )
    filtered = await api_client.get(
        "/api/v1/admin/audit",
        params={"action": "user.create"},
        headers=_bearer(admin),
    )

    assert len(all_entries.json()) == 2
    assert {entry["action"] for entry in filtered.json()} == {"user.create"}
    assert len(filtered.json()) == 3
