"""Tests for the scan job handler, enqueue helper and worker integration."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Job, Library, LibraryPath, MediaItem
from app.services.jobs.types import JobCancelled, JobContext
from app.services.jobs.worker import JobWorker
from app.services.media.probe import ProbeResult
from app.services.metadata.matching import MetadataMatcher
from app.services.scanner import jobs as scan_jobs
from app.services.scanner.jobs import enqueue_scan, scan_library_job
from app.services.scanner.scanner import LibraryScanner


class FakeProbe:
    def __init__(self) -> None:
        self.calls: list[Path] = []

    async def __call__(self, path: Path) -> ProbeResult:
        self.calls.append(path)
        return ProbeResult(
            container="matroska,webm",
            duration_seconds=8160.0,
            bit_rate=8_000_000,
            streams=[],
        )


async def _make_library(
    sessionmaker: async_sessionmaker[AsyncSession],
    root: Path,
    *,
    library_type: str = "movie",
) -> str:
    async with sessionmaker() as session:
        library = Library(name=f"Movies {uuid.uuid4().hex[:8]}", type=library_type)
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.commit()
        return library.id


def _context(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    payload: dict[str, object],
    *,
    cancelled: bool = False,
) -> JobContext:
    async def report_progress(_value: float) -> None:
        return None

    async def is_cancelled() -> bool:
        return cancelled

    return JobContext(
        job_id="test-job",
        payload=payload,
        report_progress=report_progress,
        is_cancelled=is_cancelled,
        settings=settings,
        sessionmaker=sessionmaker,
    )


def _patch_scanner(monkeypatch: pytest.MonkeyPatch, probe: FakeProbe) -> None:
    def build(context: JobContext, matcher: MetadataMatcher) -> LibraryScanner:
        return LibraryScanner(
            context.settings,
            context.sessionmaker,
            probe=probe,
            matcher=matcher,
        )

    monkeypatch.setattr(scan_jobs, "build_scanner", build)


async def test_scan_job_scans_library_and_records_status(
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    (root / "The Matrix (1999).mkv").write_bytes(b"video")
    library_id = await _make_library(db_sessionmaker, root)
    probe = FakeProbe()
    _patch_scanner(monkeypatch, probe)

    result = await scan_library_job(
        _context(db_settings, db_sessionmaker, {"library_id": library_id})
    )

    assert result["library_id"] == library_id
    assert result["added"] == 1
    assert result["probes"] == 1
    assert len(probe.calls) == 1

    async with db_sessionmaker() as session:
        library = await session.get(Library, library_id)
        assert library is not None
        assert library.last_scan_status == "ok"
        assert library.last_scan_at is not None
        items = list(await session.scalars(select(MediaItem)))
        assert len(items) == 1
        assert items[0].title == "The Matrix"


async def test_scan_job_requires_library_id(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    with pytest.raises(ValueError, match="library_id"):
        await scan_library_job(_context(db_settings, db_sessionmaker, {}))


async def test_scan_job_translates_cancellation(
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    (root / "The Matrix (1999).mkv").write_bytes(b"video")
    library_id = await _make_library(db_sessionmaker, root)
    _patch_scanner(monkeypatch, FakeProbe())

    with pytest.raises(JobCancelled):
        await scan_library_job(
            _context(
                db_settings,
                db_sessionmaker,
                {"library_id": library_id},
                cancelled=True,
            )
        )


async def test_enqueue_scan_dedupes_active_jobs(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    library_id = await _make_library(db_sessionmaker, db_settings.media_dir / "movies")

    async with db_sessionmaker() as session:
        job, created = await enqueue_scan(session, library_id)
        await session.commit()
    assert created is True
    assert job.status == "pending"

    async with db_sessionmaker() as session:
        same, created_again = await enqueue_scan(session, library_id)
        await session.commit()
    assert created_again is False
    assert same.id == job.id

    async with db_sessionmaker() as session:
        count = len(list(await session.scalars(select(Job))))
    assert count == 1


async def test_enqueue_scan_records_sorted_targets(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    library_id = await _make_library(db_sessionmaker, db_settings.media_dir / "movies")

    async with db_sessionmaker() as session:
        job, created = await enqueue_scan(session, library_id, targets=["/b", "/a", "/a"])
        await session.commit()

    assert created is True
    assert job.payload["library_id"] == library_id
    assert job.payload["targets"] == ["/a", "/b"]


async def test_worker_executes_scan_job(
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    (root / "The Matrix (1999).mkv").write_bytes(b"video")
    library_id = await _make_library(db_sessionmaker, root)
    _patch_scanner(monkeypatch, FakeProbe())

    async with db_sessionmaker() as session:
        job, _ = await enqueue_scan(session, library_id)
        await session.commit()
        job_id = job.id

    worker = JobWorker(db_settings, db_sessionmaker, worker_id="scan-test")
    await worker.drain()

    async with db_sessionmaker() as session:
        stored = await session.get(Job, job_id)
        assert stored is not None
        assert stored.status == "succeeded"
        assert stored.result is not None
        assert stored.result["added"] == 1
        assert stored.progress == 1.0


def test_scan_job_is_registered() -> None:
    from app.services.jobs.handlers import REGISTRY

    assert "scan.library" in REGISTRY
    assert REGISTRY["scan.library"].concurrency == 1
