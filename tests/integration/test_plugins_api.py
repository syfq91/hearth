"""Integration tests for plugin management API endpoints (/api/v1/plugins)."""

from __future__ import annotations

import httpx
import pytest

ADMIN = {"username": "admin", "password": "correct horse"}
VIEWER = {"username": "viewer", "password": "correct horse"}


async def _setup_admin(client: httpx.AsyncClient) -> dict[str, str]:
    res = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert res.status_code == 201
    return res.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _setup_viewer(client: httpx.AsyncClient, admin_hdr: dict[str, str]) -> dict[str, str]:
    res = await client.post(
        "/api/v1/users",
        headers=admin_hdr,
        json={"username": "viewer", "password": "correct horse", "is_admin": False},
    )
    assert res.status_code == 201
    login_res = await client.post("/api/v1/auth/login", json=VIEWER)
    assert login_res.status_code == 200
    return login_res.json()


@pytest.mark.asyncio
async def test_plugins_auth_protection(api_client: httpx.AsyncClient) -> None:
    admin_tokens = await _setup_admin(api_client)
    admin_hdr = _bearer(admin_tokens)
    viewer_tokens = await _setup_viewer(api_client, admin_hdr)
    viewer_hdr = _bearer(viewer_tokens)

    # Anonymous request
    res = await api_client.get("/api/v1/plugins")
    assert res.status_code == 401

    # Non-admin request
    res = await api_client.get("/api/v1/plugins", headers=viewer_hdr)
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_list_plugins(api_client: httpx.AsyncClient) -> None:
    admin_tokens = await _setup_admin(api_client)
    admin_hdr = _bearer(admin_tokens)

    res = await api_client.get("/api/v1/plugins", headers=admin_hdr)
    assert res.status_code == 200
    plugins = res.json()
    assert isinstance(plugins, list)
    plugin_ids = {p["id"] for p in plugins}

    assert "org.hearth.plugins.tmdb" in plugin_ids
    assert "org.hearth.plugins.musicbrainz" in plugin_ids
    assert "org.hearth.plugins.openlibrary" in plugin_ids
    assert "org.hearth.plugins.googlebooks" in plugin_ids

    tmdb = next(p for p in plugins if p["id"] == "org.hearth.plugins.tmdb")
    assert tmdb["name"] == "TheMovieDb"
    assert tmdb["category"] == "Metadata"
    assert tmdb["has_config_schema"] is True
    assert "metadata.movie" in tmdb["capabilities"]
    assert "metadata.series" in tmdb["capabilities"]


@pytest.mark.asyncio
async def test_get_plugin_detail_and_not_found(api_client: httpx.AsyncClient) -> None:
    admin_tokens = await _setup_admin(api_client)
    admin_hdr = _bearer(admin_tokens)

    # Valid plugin
    res = await api_client.get("/api/v1/plugins/org.hearth.plugins.tmdb", headers=admin_hdr)
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == "org.hearth.plugins.tmdb"
    assert data["config_schema"] is not None
    assert "api_key" in data["config_schema"]["properties"]

    # Non-existent plugin
    res = await api_client.get("/api/v1/plugins/org.hearth.plugins.nonexistent", headers=admin_hdr)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "plugin_not_found"


@pytest.mark.asyncio
async def test_get_and_update_plugin_config_secret_masking(
    api_client: httpx.AsyncClient,
) -> None:
    admin_tokens = await _setup_admin(api_client)
    admin_hdr = _bearer(admin_tokens)

    plugin_id = "org.hearth.plugins.tmdb"

    # Set new API key
    put_res = await api_client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        headers=admin_hdr,
        json={"config": {"api_key": "real-secret-key-12345", "preferred_language": "fr-FR"}},
    )
    assert put_res.status_code == 200
    put_data = put_res.json()
    assert put_data["plugin_id"] == plugin_id
    assert put_data["config"]["api_key"] == "…2345"
    assert put_data["config"]["preferred_language"] == "fr-FR"

    # Get config verifies masking
    get_res = await api_client.get(f"/api/v1/plugins/{plugin_id}/config", headers=admin_hdr)
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["config"]["api_key"] == "…2345"
    assert get_data["config"]["preferred_language"] == "fr-FR"

    # Resubmitting masked value preserves original secret
    put2_res = await api_client.put(
        f"/api/v1/plugins/{plugin_id}/config",
        headers=admin_hdr,
        json={"config": {"api_key": "…2345", "preferred_language": "es-ES"}},
    )
    assert put2_res.status_code == 200
    assert put2_res.json()["config"]["api_key"] == "…2345"
    assert put2_res.json()["config"]["preferred_language"] == "es-ES"


@pytest.mark.asyncio
async def test_enable_disable_and_reload_plugins(api_client: httpx.AsyncClient) -> None:
    admin_tokens = await _setup_admin(api_client)
    admin_hdr = _bearer(admin_tokens)

    plugin_id = "org.hearth.plugins.openlibrary"

    # Disable plugin
    dis_res = await api_client.post(f"/api/v1/plugins/{plugin_id}/disable", headers=admin_hdr)
    assert dis_res.status_code == 200
    assert dis_res.json()["status"] == "disabled"

    # Verify status in list
    list_res = await api_client.get("/api/v1/plugins", headers=admin_hdr)
    ol = next(p for p in list_res.json() if p["id"] == plugin_id)
    assert ol["status"] == "disabled"

    # Re-enable plugin
    en_res = await api_client.post(f"/api/v1/plugins/{plugin_id}/enable", headers=admin_hdr)
    assert en_res.status_code == 200
    assert en_res.json()["status"] == "active"

    # Reload plugins
    reload_res = await api_client.post("/api/v1/plugins/reload", headers=admin_hdr)
    assert reload_res.status_code == 200
    assert isinstance(reload_res.json(), list)
    assert any(p["id"] == plugin_id for p in reload_res.json())


@pytest.mark.asyncio
async def test_admin_metadata_settings_synced_to_plugin(api_client: httpx.AsyncClient) -> None:
    admin_tokens = await _setup_admin(api_client)
    admin_hdr = _bearer(admin_tokens)

    # Update via legacy /api/v1/admin/metadata/settings
    update_res = await api_client.put(
        "/api/v1/admin/metadata/settings",
        headers=admin_hdr,
        json={"tmdb_api_key": "legacy-sync-key-9999"},
    )
    assert update_res.status_code == 200

    # Verify TMDB plugin config updated with the key
    plugin_res = await api_client.get(
        "/api/v1/plugins/org.hearth.plugins.tmdb/config",
        headers=admin_hdr,
    )
    assert plugin_res.status_code == 200
    assert plugin_res.json()["config"]["api_key"] == "…9999"
