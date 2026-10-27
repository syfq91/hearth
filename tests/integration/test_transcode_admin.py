"""Integration tests for admin transcoding endpoints."""

import httpx
import pytest

ADMIN = {"username": "admin", "password": "correct horse"}
USER = {"username": "member", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
async def test_admin_transcoding_capabilities_auth(api_client: httpx.AsyncClient) -> None:
    # 401 without auth
    unauth = await api_client.get("/api/v1/admin/transcoding/capabilities")
    assert unauth.status_code == 401

    admin_tokens = await _setup(api_client)

    # Normal user 403
    create_res = await api_client.post(
        "/api/v1/users",
        json={"username": USER["username"], "password": USER["password"], "role": "user"},
        headers=_bearer(admin_tokens),
    )
    assert create_res.status_code == 201
    user_login = await api_client.post("/api/v1/auth/login", json=USER)
    assert user_login.status_code == 200
    user_tokens = user_login.json()

    user_res = await api_client.get(
        "/api/v1/admin/transcoding/capabilities",
        headers=_bearer(user_tokens),
    )
    assert user_res.status_code == 403

    # Admin 200
    admin_res = await api_client.get(
        "/api/v1/admin/transcoding/capabilities",
        headers=_bearer(admin_tokens),
    )
    assert admin_res.status_code == 200
    data = admin_res.json()
    assert "render_device" in data
    assert "available" in data
    assert "software_encoders" in data
    assert "libx264" in data["software_encoders"]
