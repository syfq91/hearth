"""Integration tests for scan reconciliation and move detection."""

import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Library, LibraryPath, MediaFile, MediaItem, User, UserItemState
from app.services.media.probe import ProbeError, ProbeResult
from app.services.scanner.scanner import LibraryScanner

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is available in CI")


class FakeProbe:
    """Deterministic probe with a call counter and optional failures."""

    def __init__(self, fail_names: set[str] | None = None) -> None:
        self.calls: list[Path] = []
        self.fail_names = fail_names or set()

    async def __call__(self, path: Path) -> ProbeResult:
        self.calls.append(path)
        if path.name in self.fail_names:
            raise ProbeError(str(path), "Invalid data found when processing input")
        return ProbeResult(
            container="matroska,webm",
            duration_seconds=123.5,
            bit_rate=8_000_000,
            streams=[],
        )


async def _make_library(
    sessionmaker: async_sessionmaker[AsyncSession], root: Path, name: str | None = None
) -> str:
    async with sessionmaker() as session:
        library = Library(name=name or f"Movies {uuid.uuid4().hex[:8]}", type="movie")
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.commit()
        return library.id


def _write_movie(root: Path, name: str, content: bytes = b"not-a-real-video") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


async def test_scan_adds_files_and_rescan_does_no_work(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    matrix = _write_movie(root, "The Matrix (1999)/The Matrix (1999).mkv")
    arrival = _write_movie(root, "Arrival (2016).mp4", b"other")
    library_id = await _make_library(db_sessionmaker, root)
    probe = FakeProbe()
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=probe)

    first = await scanner.scan_library(library_id)
    assert (first.added, first.added_items, first.probes) == (2, 2, 2)
    assert first.unchanged == 0

    second = await scanner.scan_library(library_id)
    assert second.unchanged == 2
    assert second.probes == 0
    assert len(probe.calls) == 2

    async with db_sessionmaker() as session:
        library = await session.get(Library, library_id)
        assert library is not None and library.last_scan_status == "ok"
        assert library.last_scan_stats["unchanged"] == 2
        files = list(await session.scalars(select(MediaFile)))
        assert len(files) == 2
        item_ids = {file.path: file.item_id for file in files}

    # Changing a file re-probes it but keeps its catalog item.
    matrix.write_bytes(b"changed content")
    third = await scanner.scan_library(library_id)
    assert third.updated == 1
    assert third.probes == 1
    assert len(probe.calls) == 3
    assert third.added_items == 0

    async with db_sessionmaker() as session:
        updated = await session.get(
            MediaFile,
            (await session.scalar(select(MediaFile.id).where(MediaFile.path == str(matrix)))),
        )
        assert updated is not None
        assert updated.item_id == item_ids[str(matrix)]
        assert updated.probe_error is None

    fourth = await scanner.scan_library(library_id)
    assert fourth.probes == 0
    assert len(probe.calls) == 3
    assert fourth.unchanged == 2
    assert arrival.exists()


async def test_rename_is_a_move_and_preserves_watch_state(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    original = _write_movie(root, "The Matrix (1999)/The Matrix.mkv")
    library_id = await _make_library(db_sessionmaker, root)
    probe = FakeProbe()
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=probe)
    await scanner.scan_library(library_id)

    async with db_sessionmaker() as session:
        user = User(username="watcher", password_hash="hash")
        session.add(user)
        item_id = await session.scalar(select(MediaItem.id))
        assert item_id is not None
        await session.flush()
        session.add(UserItemState(user_id=user.id, item_id=item_id, played=True, position_ms=9000))
        await session.commit()

    renamed = root / "The Matrix (1999)/The Matrix (1999) 1080p.mkv"
    original.rename(renamed)

    stats = await scanner.scan_library(library_id)
    assert stats.moved == 1
    assert stats.added == 0
    assert stats.probes == 0
    assert len(probe.calls) == 1

    async with db_sessionmaker() as session:
        files = list(await session.scalars(select(MediaFile)))
        assert len(files) == 1
        assert files[0].path == str(renamed)
        assert files[0].item_id == item_id
        state = await session.get(UserItemState, ((await session.scalar(select(User.id))), item_id))
        assert state is not None
        assert state.played is True
        assert state.position_ms == 9000


async def test_mass_deletion_is_aborted_before_writes(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    paths = [_write_movie(root, f"Movie {index:02d} ({2000 + index}).mp4") for index in range(12)]
    library_id = await _make_library(db_sessionmaker, root)
    probe = FakeProbe()
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=probe)
    await scanner.scan_library(library_id)

    for path in paths[:5]:
        path.unlink()

    stats = await scanner.scan_library(library_id)
    assert stats.aborted is True
    assert stats.abort_reason is not None
    assert "mass-deletion" in stats.abort_reason
    assert stats.removed == 0

    async with db_sessionmaker() as session:
        library = await session.get(Library, library_id)
        assert library is not None
        assert library.last_scan_status == "aborted"
        files = list(await session.scalars(select(MediaFile)))
        assert len(files) == 12
        assert all(file.missing_since is None for file in files)


async def test_probe_failure_does_not_fail_the_scan(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    _write_movie(root, "Good (2020).mkv")
    _write_movie(root, "Broken (2021).mkv")
    library_id = await _make_library(db_sessionmaker, root)
    scanner = LibraryScanner(
        db_settings, db_sessionmaker, probe=FakeProbe(fail_names={"Broken (2021).mkv"})
    )

    stats = await scanner.scan_library(library_id)
    assert stats.added == 2
    assert stats.errors == 1
    assert stats.aborted is False

    async with db_sessionmaker() as session:
        files = {
            file.path.rsplit("/", 1)[-1]: file for file in await session.scalars(select(MediaFile))
        }
        assert files["Broken (2021).mkv"].probe_error
        assert files["Good (2020).mkv"].probe_error is None
        library = await session.get(Library, library_id)
        assert library is not None and library.last_scan_status == "ok"


async def test_soft_delete_and_restore_preserve_item(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    path = _write_movie(root, "Persistent (2019).mkv")
    backup = path.with_suffix(".backup")
    shutil.copy2(path, backup)
    library_id = await _make_library(db_sessionmaker, root)
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=FakeProbe())
    await scanner.scan_library(library_id)

    path.unlink()
    missing = await scanner.scan_library(library_id)
    assert missing.missing == 1
    assert missing.removed == 0

    async with db_sessionmaker() as session:
        file = await session.scalar(select(MediaFile))
        assert file is not None and file.missing_since is not None

    shutil.copy2(backup, path)
    restored = await scanner.scan_library(library_id)
    assert restored.restored == 1
    assert restored.probes == 0

    async with db_sessionmaker() as session:
        file = await session.scalar(select(MediaFile))
        assert file is not None and file.missing_since is None
        assert await session.scalar(select(func.count()).select_from(MediaItem)) == 1


async def test_zero_grace_deletes_file_and_empty_item(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    db_settings.scan_missing_grace_hours = 0
    root = db_settings.media_dir / "movies"
    root.mkdir()
    path = _write_movie(root, "Ephemeral (2022).mkv")
    library_id = await _make_library(db_sessionmaker, root)
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=FakeProbe())
    await scanner.scan_library(library_id)

    path.unlink()
    stats = await scanner.scan_library(library_id)
    assert stats.removed == 1
    assert stats.missing == 0

    async with db_sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(MediaFile)) == 0
        assert await session.scalar(select(func.count()).select_from(MediaItem)) == 0
