"""Tests for interval-based scan scheduling."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Job, Library, LibraryPath
from app.services.scanner.scheduler import ScanScheduler

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


async def _make_library(
    sessionmaker: async_sessionmaker[AsyncSession],
    root: Path | None,
    *,
    last_scan_at: datetime | None = None,
) -> str:
    async with sessionmaker() as session:
        library = Library(
            name=f"Movies {uuid.uuid4().hex[:8]}",
            type="movie",
            last_scan_at=last_scan_at,
        )
        if root is not None:
            library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.commit()
        return library.id


async def test_tick_queues_only_due_libraries(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    due = await _make_library(db_sessionmaker, db_settings.media_dir / "due")
    recent = await _make_library(
        db_sessionmaker,
        db_settings.media_dir / "recent",
        last_scan_at=NOW - timedelta(hours=1),
    )
    stale = await _make_library(
        db_sessionmaker,
        db_settings.media_dir / "stale",
        last_scan_at=NOW - timedelta(hours=7),
    )
    without_paths = await _make_library(db_sessionmaker, None)

    scheduler = ScanScheduler(db_settings, db_sessionmaker, clock=lambda: NOW)
    job_ids = await scheduler.tick()

    assert len(job_ids) == 2
    async with db_sessionmaker() as session:
        jobs = list(await session.scalars(select(Job)))
    queued = {job.payload["library_id"] for job in jobs}
    assert queued == {due, stale}
    assert recent not in queued
    assert without_paths not in queued
    assert all(job.type == "scan.library" for job in jobs)


async def test_tick_does_not_duplicate_active_scans(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    library_id = await _make_library(db_sessionmaker, db_settings.media_dir / "movies")
    scheduler = ScanScheduler(db_settings, db_sessionmaker, clock=lambda: NOW)

    first = await scheduler.tick()
    second = await scheduler.tick()

    assert len(first) == 1
    assert second == []
    async with db_sessionmaker() as session:
        jobs = list(await session.scalars(select(Job)))
    assert len(jobs) == 1
    assert jobs[0].payload["library_id"] == library_id


async def test_tick_skips_libraries_with_fresh_scans(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    await _make_library(
        db_sessionmaker,
        db_settings.media_dir / "movies",
        last_scan_at=NOW - timedelta(minutes=5),
    )
    scheduler = ScanScheduler(db_settings, db_sessionmaker, clock=lambda: NOW)

    assert await scheduler.tick() == []
    async with db_sessionmaker() as session:
        assert list(await session.scalars(select(Job))) == []
