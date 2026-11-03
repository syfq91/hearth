"""Integration tests for Phase 10 Task 10.1: Load Test Simulation."""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Library, LibraryPath
from app.services.scanner.scanner import LibraryScanner
from scripts.load_test import execute_load_test

FIXTURE_MOVIE = Path("web/e2e/fixtures/media/movies/Embers at Dawn (2024).mp4")


@pytest.fixture
def db_settings(tmp_path: Path) -> Settings:
    """Settings fixture using host VAAPI device if present for hardware acceleration."""
    from app.services.transcode import capabilities

    capabilities._cached_capabilities = None
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    render_dev = (
        Path("/dev/dri/renderD128")
        if Path("/dev/dri/renderD128").exists()
        else tmp_path / "renderD128"
    )
    return Settings(
        env="testing",
        log_level="WARNING",
        data_dir=tmp_path,
        db_path=tmp_path / "hearth.db",
        transcode_dir=tmp_path / "transcode",
        media_dir=media_dir,
        render_device=render_dev,
        secret_key="test-secret-key",
        job_poll_interval=0.05,
    )


@pytest.mark.asyncio
async def test_load_test_simulation(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Verify 15-user scripted load test simulation passes performance targets."""
    # 1. Setup admin
    admin_auth = {"username": "admin", "password": "correct horse"}
    setup_res = await api_client.post("/api/v1/auth/setup", json=admin_auth)
    assert setup_res.status_code == 201, setup_res.text
    admin_tokens = setup_res.json()
    admin_token = admin_tokens["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. Setup movie library with fixture file
    movie_dir = db_settings.media_dir / "movies"
    movie_dir.mkdir(parents=True, exist_ok=True)
    dst_movie = movie_dir / "Embers at Dawn (2024).mp4"
    if FIXTURE_MOVIE.exists():
        shutil.copy2(FIXTURE_MOVIE, dst_movie)
    else:
        # Fallback dummy file
        dst_movie.write_bytes(b"\x00" * 1024 * 512)

    async with db_sessionmaker() as session:
        lib = Library(name="Movies", type="movie")
        lib.paths.append(LibraryPath(path=str(movie_dir)))
        session.add(lib)
        await session.commit()
        library_id = lib.id

    # 3. Scan library with real scanner
    scanner = LibraryScanner(db_settings, db_sessionmaker)
    await scanner.scan_library(library_id)

    # Fetch scanned item and file IDs
    items_res = await api_client.get(
        f"/api/v1/libraries/{library_id}/items",
        headers=admin_headers,
    )
    assert items_res.status_code == 200, items_res.text
    items = items_res.json()["items"]
    assert len(items) >= 1
    item_id = items[0]["id"]

    detail_res = await api_client.get(f"/api/v1/items/{item_id}", headers=admin_headers)
    assert detail_res.status_code == 200, detail_res.text
    files = detail_res.json().get("files", [])
    assert len(files) >= 1
    file_id = files[0]["id"]

    # 4. Provision and log in 14 users (plus admin = 15 total enabled users)
    user_tokens: list[str] = [admin_token]
    for i in range(1, 15):
        username = f"user_{i:02d}"
        pw = "correct horse"
        create_res = await api_client.post(
            "/api/v1/users",
            headers=admin_headers,
            json={
                "username": username,
                "password": pw,
                "role": "user",
                "library_ids": [library_id],
            },
        )
        assert create_res.status_code == 201, create_res.text

        login_res = await api_client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": pw},
        )
        assert login_res.status_code == 200, login_res.text
        user_tokens.append(login_res.json()["access_token"])

    assert len(user_tokens) == 15

    # Warm up transcode capabilities prior to load test run
    from app.services.transcode.capabilities import probe_transcode_capabilities

    await probe_transcode_capabilities(db_settings)

    # Warm up application endpoints and mappers prior to measurement
    warmup_headers = {"Authorization": f"Bearer {admin_token}"}
    await api_client.get("/api/v1/libraries", headers=warmup_headers)
    await api_client.get(f"/api/v1/libraries/{library_id}/items", headers=warmup_headers)
    await api_client.get(f"/api/v1/items/{item_id}", headers=warmup_headers)
    await api_client.get("/api/v1/home/continue-watching", headers=warmup_headers)
    await api_client.get("/api/v1/home/recently-added", headers=warmup_headers)
    await api_client.get("/api/v1/search?q=movie", headers=warmup_headers)
    await api_client.get(
        f"/api/v1/stream/{file_id}",
        headers={**warmup_headers, "Range": "bytes=0-524287"},
    )
    await api_client.post(
        f"/api/v1/items/{item_id}/progress",
        headers=warmup_headers,
        json={"position_ms": 1000, "state": "playing"},
    )

    # 5. Execute load test simulation
    collector = None
    from scripts.load_test import LoadTestMetricsCollector

    collector = LoadTestMetricsCollector()

    report = await execute_load_test(
        client=api_client,
        admin_token=admin_token,
        user_tokens=user_tokens,
        library_id=library_id,
        item_id=item_id,
        file_id=file_id,
        duration_seconds=20.0,
        warmup_seconds=5.0,
        metrics=collector,
    )

    print("\n" + report.to_markdown())
    t0 = collector.records[0].timestamp if collector.records else 0.0
    for r in collector.records:
        rel = r.timestamp - t0
        line = f"[{rel:4.1f}s] {r.category} {r.endpoint} {r.elapsed_ms:.1f}ms ({r.status_code})"
        print(line)

    # 6. Verify acceptance targets
    assert report.total_requests > 0
    assert report.api_requests > 0
    assert report.direct_play_bytes > 0
    assert report.target_p95_met, f"p95 was {report.api_p95_ms}ms, expected < 300ms"
    assert report.target_dropped_segments_met, f"Dropped {report.dropped_segments} segments"
    assert report.target_memory_growth_met, f"Memory slope was {report.memory_slope_mb_per_hour}"
    assert report.all_targets_met
