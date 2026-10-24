"""Phase 2 acceptance checks that need real media and real ffprobe.

The existing scanner and matcher tests use scripted probes to keep them fast and
deterministic. The plan's Phase 2 criteria also make claims about real ffprobe
work and scan duration, so those are exercised here with generated fixtures:

* an unchanged rescan performs zero ffprobe calls and zero provider calls;
* a 50-file generated library scans in under the 30 second CI budget;
* a provider failure leaves the item pending and is retried once due.
"""

import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Library, LibraryPath, MediaItem
from app.services.media.probe import ProbeResult, probe_media
from app.services.metadata.base import (
    MovieMetadata,
    ProviderUnavailable,
    SearchResult,
)
from app.services.metadata.matching import MetadataMatcher
from app.services.scanner.scanner import LibraryScanner
from tests.support import FakeProvider

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for the phase 2 acceptance scan",
)

_FIXTURE_COUNT = 50
_SCAN_BUDGET_SECONDS = 30.0


class CountingProbe:
    """Real ffprobe with a call counter, so "zero probes" is measured."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.calls = 0

    async def __call__(self, path: Path) -> ProbeResult:
        self.calls += 1
        return await probe_media(
            path,
            ffprobe_binary=self.settings.ffprobe_binary,
            timeout=self.settings.ffprobe_timeout_seconds,
        )


class StubProbe:
    """Cheap probe for tests that only care about matching behaviour."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, path: Path) -> ProbeResult:
        self.calls += 1
        return ProbeResult(
            container="matroska,webm",
            duration_seconds=8160.0,
            bit_rate=8_000_000,
            streams=[],
        )


def _encode_tiny_video(destination: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=d=1:s=160x120:r=5",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(destination),
        ],
        check=True,
        capture_output=True,
    )


def _generate_movie_library(root: Path, count: int) -> list[Path]:
    """One real encode copied into ``count`` uniquely named library files."""

    source = root / ".fixture.mp4"
    _encode_tiny_video(source)
    payload = source.read_bytes()
    paths: list[Path] = []
    for index in range(count):
        year = 1970 + index
        path = root / f"Library Movie {index:02d} ({year}).mp4"
        path.write_bytes(payload)
        paths.append(path)
    source.unlink()
    return paths


async def _make_library(sessionmaker: async_sessionmaker[AsyncSession], root: Path) -> str:
    async with sessionmaker() as session:
        library = Library(name=f"Movies {uuid.uuid4().hex[:8]}", type="movie")
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.commit()
        return library.id


def _make_matcher(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    provider: FakeProvider,
) -> MetadataMatcher:
    return MetadataMatcher(settings, sessionmaker, providers=[provider])


async def test_fifty_file_library_scans_within_budget_and_rescan_is_noop(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    _generate_movie_library(root, _FIXTURE_COUNT)
    library_id = await _make_library(db_sessionmaker, root)

    provider = FakeProvider(results=[])
    matcher = _make_matcher(db_settings, db_sessionmaker, provider)
    probe = CountingProbe(db_settings)
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=probe, matcher=matcher)

    started = time.monotonic()
    first = await scanner.scan_library(library_id)
    elapsed = time.monotonic() - started

    assert first.added == _FIXTURE_COUNT
    assert first.probes == _FIXTURE_COUNT
    assert probe.calls == _FIXTURE_COUNT
    assert first.errors == 0
    assert first.unmatched == _FIXTURE_COUNT
    assert provider.search_calls == _FIXTURE_COUNT
    assert elapsed < _SCAN_BUDGET_SECONDS, (
        f"first scan took {elapsed:.2f}s, budget is {_SCAN_BUDGET_SECONDS:.0f}s"
    )
    assert first.duration_seconds < _SCAN_BUDGET_SECONDS

    # The acceptance criterion: no ffprobe and no provider work when nothing changed.
    second = await scanner.scan_library(library_id)
    assert second.unchanged == _FIXTURE_COUNT
    assert second.probes == 0
    assert probe.calls == _FIXTURE_COUNT
    assert second.provider_calls == 0
    assert provider.search_calls == _FIXTURE_COUNT

    async with db_sessionmaker() as session:
        assert await session.scalar(select(MediaItem).limit(1)) is not None


async def test_provider_failure_is_retried_once_the_backoff_expires(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    (root / "The Matrix (1999).mkv").write_bytes(b"video")
    library_id = await _make_library(db_sessionmaker, root)

    provider = FakeProvider(error=ProviderUnavailable("TMDB API key is not configured"))
    matcher = _make_matcher(db_settings, db_sessionmaker, provider)
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=StubProbe(), matcher=matcher)

    first = await scanner.scan_library(library_id)
    assert first.added == 1
    assert first.aborted is False
    assert first.metadata_pending == 1
    assert provider.search_calls == 1

    async with db_sessionmaker() as session:
        item = await session.scalar(select(MediaItem))
        assert item is not None
        assert item.match_state == "pending"
        assert item.next_match_at is not None
        assert item.next_match_at > datetime.now(UTC)

    # Nothing is attempted while the backoff is still running.
    assert (await matcher.match_pending()).attempted == 0
    assert provider.search_calls == 1

    # When the retry time arrives, the item is attempted again and can succeed.
    async with db_sessionmaker() as session:
        item = await session.scalar(select(MediaItem))
        assert item is not None
        item.next_match_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.commit()

    provider.error = None
    provider.results = [SearchResult("tmdb", "603", "The Matrix", 1999)]
    provider.details = {
        "603": MovieMetadata(
            provider="tmdb",
            provider_id="603",
            title="The Matrix",
            year=1999,
            runtime_seconds=8160,
        )
    }

    second = await scanner.scan_library(library_id)
    assert second.matched == 1
    assert second.metadata_pending == 0
    assert provider.search_calls == 2

    async with db_sessionmaker() as session:
        item = await session.scalar(select(MediaItem))
        assert item is not None
        assert item.match_state == "matched"
        assert item.match_error is None
        assert item.next_match_at is None
        assert item.matched_at is not None
