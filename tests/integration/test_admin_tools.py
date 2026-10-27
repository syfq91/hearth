"""Integration tests for administrator operations and diagnostics."""

import logging

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Library, MediaItem, Movie
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}
VIEWER = {"username": "viewer", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    res = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert res.status_code == 201
    return res.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _login(client: httpx.AsyncClient, username: str) -> dict[str, str]:
    res = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "correct horse"},
    )
    assert res.status_code == 200
    return res.json()


@pytest.mark.asyncio
async def test_admin_scan_controls(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    admin_tokens = await _setup(api_client)
    admin_hdr = _bearer(admin_tokens)

    # Create library
    async with db_sessionmaker() as session:
        lib = Library(name="Admin Scan Library", type="movie")
        session.add(lib)
        await session.commit()
        lib_id = lib.id

    # Create a non-admin user
    u_res = await api_client.post(
        "/api/v1/users",
        headers=admin_hdr,
        json={"username": "viewer", "password": "correct horse", "role": "user"},
    )
    assert u_res.status_code == 201
    viewer_tokens = await _login(api_client, "viewer")
    viewer_hdr = _bearer(viewer_tokens)

    # 1. Non-admin forbidden
    bad_scan = await api_client.post(f"/api/v1/admin/libraries/{lib_id}/scan", headers=viewer_hdr)
    assert bad_scan.status_code == 403

    # 2. Admin single scan
    scan_single = await api_client.post(f"/api/v1/admin/libraries/{lib_id}/scan", headers=admin_hdr)
    assert scan_single.status_code == 202
    assert scan_single.json()["type"] == "scan.library"

    # 3. Admin scan all
    scan_all = await api_client.post("/api/v1/admin/libraries/scan", headers=admin_hdr)
    assert scan_all.status_code == 202
    assert len(scan_all.json()) >= 1


@pytest.mark.asyncio
async def test_admin_unmatched_and_rematch(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    admin_tokens = await _setup(api_client)
    admin_hdr = _bearer(admin_tokens)

    # Add an unmatched movie
    async with db_sessionmaker() as session:
        lib = Library(name="Unmatched Lib", type="movie")
        session.add(lib)
        await session.flush()

        item = MediaItem(
            library_id=lib.id,
            type="movie",
            title="Unknown Movie 123",
            sort_title=sort_title("Unknown Movie 123"),
            match_state="unmatched",
            match_attempts=2,
            match_error="No match found on TMDB",
        )
        session.add(item)
        await session.flush()
        session.add(Movie(item_id=item.id))
        await session.commit()
        item_id = item.id

    # 1. List unmatched
    res = await api_client.get("/api/v1/admin/unmatched", headers=admin_hdr)
    assert res.status_code == 200
    unmatched_list = res.json()
    assert len(unmatched_list) == 1
    assert unmatched_list[0]["id"] == item_id
    assert unmatched_list[0]["match_state"] == "unmatched"

    # 2. Rematch with hints
    rematch_res = await api_client.post(
        f"/api/v1/admin/items/{item_id}/rematch",
        headers=admin_hdr,
        json={"title": "The Matrix", "year": 1999, "provider_id": "603"},
    )
    assert rematch_res.status_code == 200
    rematched = rematch_res.json()
    assert rematched["title"] == "The Matrix"
    assert rematched["year"] == 1999
    assert rematched["match_state"] == "pending"
    assert rematched["match_attempts"] == 0
    assert rematched["match_error"] is None


@pytest.mark.asyncio
async def test_admin_system_info_and_logs(
    api_client: httpx.AsyncClient,
) -> None:
    admin_tokens = await _setup(api_client)
    admin_hdr = _bearer(admin_tokens)

    # 1. System info
    res = await api_client.get("/api/v1/admin/system/info", headers=admin_hdr)
    assert res.status_code == 200
    info = res.json()
    assert "os_platform" in info
    assert "python_version" in info
    assert "sqlite_version" in info
    assert "media_disk" in info
    assert info["pid"] > 0
    assert info["memory_rss_bytes"] > 0

    # 2. Trigger log entry and inspect log tail
    logger = logging.getLogger("hearth.test")
    logger.warning("Admin test log message payload 98765")

    log_res = await api_client.get("/api/v1/admin/logs?limit=50", headers=admin_hdr)
    assert log_res.status_code == 200
    logs = log_res.json()
    assert any("Admin test log message payload 98765" in entry["message"] for entry in logs)


@pytest.mark.asyncio
async def test_admin_transcode_settings(
    api_client: httpx.AsyncClient,
) -> None:
    admin_tokens = await _setup(api_client)
    admin_hdr = _bearer(admin_tokens)

    # 1. Read settings
    get_res = await api_client.get("/api/v1/admin/transcoding/settings", headers=admin_hdr)
    assert get_res.status_code == 200
    curr = get_res.json()
    assert "max_hw_sessions" in curr
    assert "max_sw_sessions" in curr

    # 2. Update settings
    put_res = await api_client.put(
        "/api/v1/admin/transcoding/settings",
        headers=admin_hdr,
        json={"force_software": True, "max_sw_sessions": 6},
    )
    assert put_res.status_code == 200
    updated = put_res.json()
    assert updated["force_software"] is True
    assert updated["max_sw_sessions"] == 6
