"""Integration tests for the manual library scan endpoint."""

import httpx

from app.core.config import Settings

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _create_library(
    client: httpx.AsyncClient,
    admin: dict[str, str],
    path: str,
    *,
    name: str = "Movies",
    library_type: str = "movie",
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/libraries",
        json={"name": name, "type": library_type, "paths": [path]},
        headers=_bearer(admin),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_scan_endpoint_queues_and_reuses_a_job(
    api_client: httpx.AsyncClient, db_settings: Settings
) -> None:
    admin = await _setup(api_client)
    root = db_settings.media_dir / "movies"
    root.mkdir()
    library = await _create_library(api_client, admin, str(root))

    first = await api_client.post(f"/api/v1/libraries/{library['id']}/scan", headers=_bearer(admin))
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["created"] is True
    assert body["status"] == "pending"
    assert body["job_id"]

    second = await api_client.post(
        f"/api/v1/libraries/{library['id']}/scan", headers=_bearer(admin)
    )
    assert second.status_code == 202
    assert second.json()["created"] is False
    assert second.json()["job_id"] == body["job_id"]

    job = await api_client.get(f"/api/v1/jobs/{body['job_id']}", headers=_bearer(admin))
    assert job.status_code == 200
    payload = job.json()
    assert payload["type"] == "scan.library"
    assert payload["payload"]["library_id"] == library["id"]


async def test_scan_endpoint_requires_admin(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post("/api/v1/libraries/whatever/scan")
    assert response.status_code == 401


async def test_scan_endpoint_unknown_library_is_404(
    api_client: httpx.AsyncClient,
) -> None:
    admin = await _setup(api_client)
    response = await api_client.post(
        "/api/v1/libraries/does-not-exist/scan", headers=_bearer(admin)
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "library_not_found"


async def test_scan_endpoint_rejects_unsupported_library_type(
    api_client: httpx.AsyncClient, db_settings: Settings
) -> None:
    admin = await _setup(api_client)
    root = db_settings.media_dir / "tv"
    root.mkdir()
    library = await _create_library(api_client, admin, str(root), name="TV", library_type="tv")

    response = await api_client.post(
        f"/api/v1/libraries/{library['id']}/scan", headers=_bearer(admin)
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_library_type"
