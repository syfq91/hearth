"""Tests for the library watcher's event grouping."""

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from watchfiles import Change

from app.core.config import Settings
from app.db.models import Job, Library, LibraryPath
from app.services.scanner.watcher import LibraryWatcher


async def _make_library(sessionmaker: async_sessionmaker[AsyncSession], root: Path) -> str:
    async with sessionmaker() as session:
        library = Library(name=f"Movies {uuid.uuid4().hex[:8]}", type="movie")
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.commit()
        return library.id


async def _jobs(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> list[Job]:
    async with sessionmaker() as session:
        return list(await session.scalars(select(Job).order_by(Job.created_at)))


async def test_changes_enqueue_one_targeted_scan_per_library(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    (root / "Action").mkdir(parents=True)
    library_id = await _make_library(db_sessionmaker, root)
    watcher = LibraryWatcher(db_settings, db_sessionmaker)

    job_ids = await watcher.handle_changes(
        {
            (Change.added, str(root / "Action" / "The Matrix (1999).mkv")),
            (Change.modified, str(root / "Action" / "Heat (1995).mkv")),
            (Change.added, str(root / "Arrival (2016).mkv")),
        }
    )

    assert len(job_ids) == 1
    jobs = await _jobs(db_sessionmaker)
    assert len(jobs) == 1
    job = jobs[0]
    assert job.type == "scan.library"
    assert job.payload["library_id"] == library_id
    assert job.payload["targets"] == [str(root), str(root / "Action")]


async def test_irrelevant_files_and_paths_outside_roots_are_ignored(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    await _make_library(db_sessionmaker, root)
    outside = db_settings.media_dir / "elsewhere"
    outside.mkdir()
    watcher = LibraryWatcher(db_settings, db_sessionmaker)

    job_ids = await watcher.handle_changes(
        {
            (Change.added, str(root / "notes.txt")),
            (Change.added, str(outside / "Other Movie.mkv")),
        }
    )

    assert job_ids == []
    assert await _jobs(db_sessionmaker) == []


async def test_nested_library_roots_choose_the_most_specific(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    outer = db_settings.media_dir / "movies"
    inner = outer / "kids"
    inner.mkdir(parents=True)
    await _make_library(db_sessionmaker, outer)
    inner_id = await _make_library(db_sessionmaker, inner)
    watcher = LibraryWatcher(db_settings, db_sessionmaker)

    job_ids = await watcher.handle_changes({(Change.added, str(inner / "Finding Nemo (2003).mkv"))})

    assert len(job_ids) == 1
    jobs = await _jobs(db_sessionmaker)
    assert jobs[0].payload["library_id"] == inner_id


async def test_deleted_file_targets_its_parent_directory(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    await _make_library(db_sessionmaker, root)
    watcher = LibraryWatcher(db_settings, db_sessionmaker)

    await watcher.handle_changes({(Change.deleted, str(root / "The Matrix (1999).mkv"))})

    jobs = await _jobs(db_sessionmaker)
    assert jobs[0].payload["targets"] == [str(root)]


async def test_pending_scan_suppresses_watcher_duplicates(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    await _make_library(db_sessionmaker, root)
    watcher = LibraryWatcher(db_settings, db_sessionmaker)

    first = await watcher.handle_changes({(Change.added, str(root / "The Matrix (1999).mkv"))})
    second = await watcher.handle_changes({(Change.added, str(root / "Heat (1995).mkv"))})

    assert len(first) == 1
    assert second == []
    assert len(await _jobs(db_sessionmaker)) == 1


async def test_missing_roots_are_not_watched(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    missing = db_settings.media_dir / "not-mounted"
    await _make_library(db_sessionmaker, missing)
    watcher = LibraryWatcher(db_settings, db_sessionmaker)

    assert await watcher._roots() == {}
    assert await watcher.handle_changes({(Change.added, str(missing / "Movie.mkv"))}) == []
