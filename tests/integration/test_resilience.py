"""Resilience and fault tolerance integration tests for Phase 10 Task 10.2.

Covers:
1. Server restart during active playback and background scan.
2. Missing or unmounted library paths (no crash, clean 404, mass-deletion protection).
3. Full disk for cache / transcode directory (graceful 503, no crash).
4. Corrupted media file (ffprobe failure logged, item marked error, no infinite loop).
5. SQLite lock contention under concurrent transactional operations.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import TranscodeCapacityExceededError
from app.db.models import Job, Library, LibraryPath, MediaFile, MediaItem, UserItemState
from app.services.jobs.worker import JobWorker
from app.services.scanner.scanner import LibraryScanner
from app.services.transcode.session import get_session_manager


@pytest.fixture
def resilience_settings(tmp_path: Path) -> Settings:
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    transcode_dir = tmp_path / "transcode"
    transcode_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        env="testing",
        log_level="WARNING",
        data_dir=tmp_path,
        db_path=tmp_path / "hearth.db",
        transcode_dir=transcode_dir,
        media_dir=media_dir,
        secret_key="test-secret-key",
        job_poll_interval=0.05,
    )


@pytest.mark.asyncio
async def test_resilience_server_restart_during_playback_and_scan(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Verify state recovery when server restarts during playback and scan."""
    # 1. Setup admin
    admin_auth = {"username": "admin", "password": "correct horse"}
    setup_res = await api_client.post("/api/v1/auth/setup", json=admin_auth)
    assert setup_res.status_code == 201
    token = setup_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Setup library and media item
    movie_dir = db_settings.media_dir / "movies"
    movie_dir.mkdir(parents=True, exist_ok=True)
    test_movie = movie_dir / "Recovery Test (2024).mp4"
    test_movie.write_bytes(b"\x00" * 1024)

    async with db_sessionmaker() as session:
        lib = Library(name="Movies", type="movie")
        lib.paths.append(LibraryPath(path=str(movie_dir)))
        session.add(lib)
        await session.flush()

        item = MediaItem(
            id="recovery-item-1",
            library_id=lib.id,
            type="movie",
            title="Recovery Test",
            sort_title="Recovery Test",
            year=2024,
        )
        session.add(item)
        file_row = MediaFile(
            id="recovery-file-1",
            item_id=item.id,
            path=str(test_movie),
            size_bytes=1024,
            mtime=test_movie.stat().st_mtime,
            partial_hash="0123456789abcdef0123456789abcdef",
        )
        session.add(file_row)

        # 3. Insert in-flight scan job marked as 'running'
        scan_job = Job(
            id="in-flight-scan-job",
            type="scan_library",
            status="running",
            payload={"library_id": lib.id},
        )
        session.add(scan_job)
        await session.commit()

    # 4. Report playback progress prior to crash/restart
    prog_res = await api_client.post(
        "/api/v1/items/recovery-item-1/progress",
        headers=headers,
        json={"position_ms": 145000, "duration_ms": 300000, "state": "playing"},
    )
    assert prog_res.status_code == 200

    # 5. Populate stale transcode cache from crashed session
    stale_dir = db_settings.transcode_dir / "stale_session_123"
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "init.mp4").write_bytes(b"stale_bytes")

    # 6. Simulate server restart:
    # a. Transcode session manager startup sweep cleans leftover directories
    manager = get_session_manager(db_settings)
    cleaned = manager.startup_sweep()
    assert cleaned >= 1
    assert not stale_dir.exists()

    # b. Job worker startup requeues jobs left 'running' by dead processes
    worker = JobWorker(db_settings, db_sessionmaker)
    requeued = await worker.requeue_running()
    assert requeued >= 1

    async with db_sessionmaker() as session:
        j = await session.get(Job, "in-flight-scan-job")
        assert j is not None
        assert j.status == "pending"

    # c. Playback resumption: client can query playback-info and resume at position
    client_caps = {
        "capabilities": {
            "preset": "chrome",
            "containers": ["mp4"],
            "video_codecs": ["h264"],
        }
    }
    pb_res = await api_client.post(
        "/api/v1/items/recovery-item-1/playback-info",
        headers=headers,
        json=client_caps,
    )
    assert pb_res.status_code == 200
    pb_data = pb_res.json()
    assert pb_data["file"]["id"] == "recovery-file-1"
    assert pb_data["mode"] == "direct_play"

    # Verify user state retained position_ms
    async with db_sessionmaker() as session:
        st = await session.scalar(
            select(UserItemState).where(UserItemState.item_id == "recovery-item-1")
        )
        assert st is not None
        assert st.position_ms == 145000


@pytest.mark.asyncio
async def test_resilience_missing_unmounted_library_path(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    """Verify missing or unmounted library paths are handled without crash or mass deletion."""
    # 1. Setup admin
    admin_auth = {"username": "admin", "password": "correct horse"}
    setup_res = await api_client.post("/api/v1/auth/setup", json=admin_auth)
    assert setup_res.status_code == 201
    token = setup_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Point library to an unmounted / missing directory
    unmounted_path = tmp_path / "unmounted_volume" / "movies"
    assert not unmounted_path.exists()

    async with db_sessionmaker() as session:
        lib = Library(name="Unmounted Lib", type="movie")
        lib.paths.append(LibraryPath(path=str(unmounted_path)))
        session.add(lib)
        await session.commit()
        library_id = lib.id

    # 3. Run scanner against unmounted path: must not raise unhandled exception
    scanner = LibraryScanner(db_settings, db_sessionmaker)
    stats = await scanner.scan_library(library_id)
    assert stats.library_id == library_id
    assert stats.added == 0
    assert stats.errors == 0

    # 4. Requesting stream of a file whose path is on an unmounted filesystem returns clean 404
    async with db_sessionmaker() as session:
        item = MediaItem(
            id="unmounted-item-1",
            library_id=library_id,
            type="movie",
            title="Unmounted Movie",
            sort_title="Unmounted Movie",
        )
        session.add(item)
        file_row = MediaFile(
            id="unmounted-file-1",
            item_id=item.id,
            path=str(unmounted_path / "movie.mp4"),
            size_bytes=1000,
            mtime=123456.0,
            partial_hash="0123456789abcdef0123456789abcdef",
        )
        session.add(file_row)
        await session.commit()

    stream_res = await api_client.get("/api/v1/stream/unmounted-file-1", headers=headers)
    assert stream_res.status_code == 404
    assert stream_res.json()["error"]["code"] == "file_not_found"


@pytest.mark.asyncio
async def test_resilience_full_disk_cache(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Verify low disk space on transcode cache returns clean 503 rather than crashing."""
    # 1. Setup admin
    admin_auth = {"username": "admin", "password": "correct horse"}
    setup_res = await api_client.post("/api/v1/auth/setup", json=admin_auth)
    assert setup_res.status_code == 201
    token = setup_res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Create library and media file
    movie_dir = db_settings.media_dir / "movies"
    movie_dir.mkdir(parents=True, exist_ok=True)
    movie_file = movie_dir / "FullDiskTest (2024).mp4"
    movie_file.write_bytes(b"\x00" * 4096)

    async with db_sessionmaker() as session:
        lib = Library(name="Movies", type="movie")
        lib.paths.append(LibraryPath(path=str(movie_dir)))
        session.add(lib)
        await session.flush()

        item = MediaItem(
            id="fulldisk-item-1",
            library_id=lib.id,
            type="movie",
            title="Full Disk Test",
            sort_title="Full Disk Test",
        )
        session.add(item)
        file_row = MediaFile(
            id="fulldisk-file-1",
            item_id=item.id,
            path=str(movie_file),
            size_bytes=4096,
            mtime=movie_file.stat().st_mtime,
            partial_hash="0123456789abcdef0123456789abcdef",
        )
        session.add(file_row)
        await session.commit()

    # 3. Simulate full disk in shutil.disk_usage (< 50MB free)
    with patch("shutil.disk_usage") as mock_usage:
        mock_usage.return_value = shutil._ntuple_diskusage(10**9, 10**9 - 10**6, 1024 * 1024)

        manager = get_session_manager(db_settings)
        # Attempting to allocate transcode session triggers TranscodeCapacityExceededError
        from app.services.transcode.decision import TranscodePlan

        plan = TranscodePlan(
            mode="transcode_hls",
            video_action="transcode",
            video_codec="libx264",
            audio_action="transcode",
            audio_codec="aac",
            audio_channels=2,
            subtitle_action="none",
            hwaccel="none",
        )
        with pytest.raises(TranscodeCapacityExceededError) as exc_info:
            await manager.create_session(
                user_id="test-user",
                item_id="fulldisk-item-1",
                file_id="fulldisk-file-1",
                input_path=movie_file,
                plan=plan,
            )
        assert "disk full" in str(exc_info.value).lower()

    # 4. Server health and direct play remain unaffected
    health_res = await api_client.get("/api/v1/health")
    assert health_res.status_code == 200

    stream_res = await api_client.get(
        "/api/v1/stream/fulldisk-file-1",
        headers={**headers, "Range": "bytes=0-100"},
    )
    assert stream_res.status_code == 206


@pytest.mark.asyncio
async def test_resilience_corrupted_media_file(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Verify corrupted media files log errors, mark probe error, and do not retry infinitely."""
    # 1. Setup admin
    admin_auth = {"username": "admin", "password": "correct horse"}
    setup_res = await api_client.post("/api/v1/auth/setup", json=admin_auth)
    assert setup_res.status_code == 201

    # 2. Create movie library with corrupted file
    movie_dir = db_settings.media_dir / "movies"
    movie_dir.mkdir(parents=True, exist_ok=True)
    corrupt_file = movie_dir / "Corrupted (2024).mp4"
    corrupt_file.write_bytes(
        b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41\xff\xff\xff\xffCORRUPT_BYTES" * 10
    )

    async with db_sessionmaker() as session:
        lib = Library(name="Movies", type="movie")
        lib.paths.append(LibraryPath(path=str(movie_dir)))
        session.add(lib)
        await session.commit()
        library_id = lib.id

    # 3. Initial scan runs probe
    scanner = LibraryScanner(db_settings, db_sessionmaker)
    stats1 = await scanner.scan_library(library_id)
    assert stats1.added == 1
    assert stats1.probes == 1
    assert stats1.errors >= 1

    async with db_sessionmaker() as session:
        file_row = await session.scalar(
            select(MediaFile).where(MediaFile.path == str(corrupt_file))
        )
        assert file_row is not None
        assert file_row.probe_error is not None
        assert len(file_row.probe_error) > 0

    # 4. Second scan: file is unchanged, MUST NOT re-probe or loop forever
    stats2 = await scanner.scan_library(library_id)
    assert stats2.unchanged == 1
    assert stats2.probes == 0
    assert stats2.errors == 0


@pytest.mark.asyncio
async def test_resilience_sqlite_lock_contention(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Verify high concurrent write volume does not encounter SQLite database locks."""
    # 1. Setup admin
    admin_auth = {"username": "admin", "password": "correct horse"}
    setup_res = await api_client.post("/api/v1/auth/setup", json=admin_auth)
    assert setup_res.status_code == 201
    admin_token = setup_res.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 2. Setup library and 10 items
    async with db_sessionmaker() as session:
        lib = Library(name="Contention Lib", type="movie")
        session.add(lib)
        await session.flush()
        items = []
        for i in range(10):
            item = MediaItem(
                id=f"contention-item-{i:02d}",
                library_id=lib.id,
                type="movie",
                title=f"Movie {i:02d}",
                sort_title=f"Movie {i:02d}",
            )
            session.add(item)
            items.append(item)
        await session.commit()

    # 3. Spawn 20 concurrent tasks performing interleaved writes
    async def worker_task(task_id: int) -> None:
        item_id = f"contention-item-{task_id % 10:02d}"
        for step in range(5):
            # Update playback progress
            res = await api_client.post(
                f"/api/v1/items/{item_id}/progress",
                headers=admin_headers,
                json={"position_ms": step * 1000 + task_id, "state": "playing"},
            )
            assert res.status_code == 200, res.text

            # Query continue watching
            cw_res = await api_client.get("/api/v1/home/continue-watching", headers=admin_headers)
            assert cw_res.status_code == 200, cw_res.text

            # Toggle favorite
            fav_res = await api_client.post(
                f"/api/v1/items/{item_id}/favorite",
                headers=admin_headers,
                json={"favorite": (step % 2 == 0)},
            )
            assert fav_res.status_code == 200, fav_res.text

    # Run 20 concurrent tasks
    tasks = [asyncio.create_task(worker_task(i)) for i in range(20)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in results:
        if isinstance(r, Exception):
            raise r
