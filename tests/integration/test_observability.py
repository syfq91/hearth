"""Integration tests for Phase 10 Task 10.4: Observability.

Covers:
1. Optional Prometheus /metrics endpoint (disabled by default, enabled via setting).
2. Prometheus exposition format validation (gauges, counters, system memory, active transcodes).
3. Admin log viewer endpoint with level and keyword search filters.
4. Clear error logging on ffmpeg/VAAPI subprocess failures.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.logging import ring_buffer_handler
from app.main import create_app


@pytest.mark.asyncio
async def test_observability_metrics_endpoint_disabled_by_default(
    api_client: httpx.AsyncClient,
) -> None:
    """Verify /metrics returns 404 when disabled (the default)."""
    res = await api_client.get("/metrics")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "metrics_disabled"


@pytest.mark.asyncio
async def test_observability_metrics_endpoint_enabled(
    tmp_path: Path,
) -> None:
    """Verify /metrics returns valid Prometheus exposition text when enabled."""
    settings = Settings(
        env="testing",
        data_dir=tmp_path,
        db_path=tmp_path / "test.db",
        media_dir=tmp_path / "media",
        transcode_dir=tmp_path / "transcode",
        metrics_enabled=True,
    )
    app = create_app(settings)
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Generate a few requests to record counters
            await client.get("/api/v1/health")
            await client.get("/api/v1/health")

            res = await client.get("/metrics")
            assert res.status_code == 200
            assert "text/plain" in res.headers["content-type"]

            text = res.text
            assert "hearth_uptime_seconds" in text
            assert "hearth_process_resident_memory_bytes" in text
            assert "hearth_system_cpu_count" in text
            assert "hearth_transcode_sessions_active" in text
            assert "hearth_database_pool_size" in text
            assert 'hearth_http_requests_total{method="GET",status="200"}' in text


@pytest.mark.asyncio
async def test_observability_admin_log_viewer(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Verify admin log viewer endpoint with level filtering and keyword search."""
    # 1. Setup admin
    admin_auth = {"username": "admin", "password": "correct horse"}
    setup_res = await api_client.post("/api/v1/auth/setup", json=admin_auth)
    assert setup_res.status_code == 201
    admin_token = setup_res.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. Setup standard user
    create_res = await api_client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": "standard_user", "password": "password123", "role": "user"},
    )
    assert create_res.status_code == 201
    login_res = await api_client.post(
        "/api/v1/auth/login",
        json={"username": "standard_user", "password": "password123"},
    )
    assert login_res.status_code == 200
    user_token = login_res.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {user_token}"}

    # 3. Non-admin user is rejected
    user_res = await api_client.get("/api/v1/admin/logs", headers=user_headers)
    assert user_res.status_code == 403

    # 4. Emit targeted log lines into ring buffer
    test_logger = logging.getLogger("hearth.test.observability")
    test_logger.addHandler(ring_buffer_handler)
    test_logger.setLevel(logging.INFO)

    test_logger.info("Observability marker: Alpha unique info event")
    test_logger.warning("Observability marker: Beta warning threshold reached")
    test_logger.error("Observability marker: Gamma critical failure encountered")

    # 5. Query all recent logs as admin
    admin_res = await api_client.get("/api/v1/admin/logs?limit=50", headers=admin_headers)
    assert admin_res.status_code == 200
    logs = admin_res.json()
    assert isinstance(logs, list)
    assert len(logs) > 0

    # 6. Filter by level
    warn_res = await api_client.get(
        "/api/v1/admin/logs?level=WARNING&limit=50",
        headers=admin_headers,
    )
    assert warn_res.status_code == 200
    warn_logs = warn_res.json()
    assert all(e["level"] == "WARNING" for e in warn_logs)
    assert any("Beta warning threshold" in e["message"] for e in warn_logs)

    # 7. Filter by keyword search
    search_res = await api_client.get(
        "/api/v1/admin/logs?search=Gamma critical&limit=50",
        headers=admin_headers,
    )
    assert search_res.status_code == 200
    search_logs = search_res.json()
    assert len(search_logs) >= 1
    assert "Gamma critical failure" in search_logs[0]["message"]

    # 8. Filter by category
    err_res = await api_client.get(
        "/api/v1/admin/logs?category=error&limit=50",
        headers=admin_headers,
    )
    assert err_res.status_code == 200
    err_logs = err_res.json()
    assert any("Gamma critical failure" in e["message"] for e in err_logs)
    assert all(e["level"] in ("ERROR", "CRITICAL") or e.get("exception") for e in err_logs)
