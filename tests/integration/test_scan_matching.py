"""Integration tests for scanner-driven metadata matching."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Genre, Library, LibraryPath, MediaItem
from app.services.media.probe import ProbeResult
from app.services.metadata.base import (
    MovieMetadata,
    PersonCredit,
    ProviderImage,
    ProviderUnavailable,
    SearchResult,
)
from app.services.metadata.local import LocalProvider
from app.services.metadata.matching import MetadataMatcher
from app.services.scanner.scanner import LibraryScanner
from tests.support import FakeArtwork, FakeProvider


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


def _matrix_details() -> MovieMetadata:
    return MovieMetadata(
        provider="tmdb",
        provider_id="603",
        title="The Matrix",
        year=1999,
        runtime_seconds=8160,
        release_date="1999-03-31",
        overview="A hacker learns the truth.",
        rating=8.2,
        genres=("Action", "Science Fiction"),
        credits=(
            PersonCredit(name="Keanu Reeves", role="cast", character="Neo", provider_id="6384"),
            PersonCredit(name="Lana Wachowski", role="director", provider_id="9330"),
        ),
        provider_ids={"tmdb": "603", "imdb": "tt0133093"},
    )


async def _make_library(
    sessionmaker: async_sessionmaker[AsyncSession], root: Path, library_type: str = "movie"
) -> str:
    async with sessionmaker() as session:
        library = Library(name=f"{library_type.title()} {uuid.uuid4().hex[:8]}", type=library_type)
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.commit()
        return library.id


async def test_scan_matches_new_item_and_rescan_is_quiet(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    (root / "The Matrix (1999).mkv").write_bytes(b"video")
    library_id = await _make_library(db_sessionmaker, root)

    provider = FakeProvider(
        results=[SearchResult("tmdb", "603", "The Matrix", 1999)],
        details={"603": _matrix_details()},
        images=[ProviderImage(kind="poster", source_url="https://img/p.jpg", is_primary=True)],
    )
    artwork = FakeArtwork()
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[provider], artwork=artwork)
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=FakeProbe(), matcher=matcher)

    first = await scanner.scan_library(library_id)
    assert first.added == 1
    assert first.matched == 1
    assert first.provider_calls == 3
    assert provider.search_calls == 1
    assert len(artwork.calls) == 1

    async with db_sessionmaker() as session:
        item = await session.scalar(select(MediaItem))
        assert item is not None
        assert item.match_state == "matched"
        assert item.title == "The Matrix"
        assert item.overview == "A hacker learns the truth."
        genres = list(await session.scalars(select(Genre)))
        assert {genre.name for genre in genres} == {"Action", "Science Fiction"}

    # The acceptance criterion: an unchanged rescan makes zero provider calls.
    second = await scanner.scan_library(library_id)
    assert second.unchanged == 1
    assert second.matched == 0
    assert second.provider_calls == 0
    assert provider.search_calls == 1
    assert provider.detail_calls == 1
    assert provider.image_calls == 1


async def test_provider_failure_leaves_scan_healthy_and_item_pending(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    root.mkdir()
    (root / "The Matrix (1999).mkv").write_bytes(b"video")
    library_id = await _make_library(db_sessionmaker, root)

    provider = FakeProvider(error=ProviderUnavailable("TMDB API key is not configured"))
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[provider])
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=FakeProbe(), matcher=matcher)

    first = await scanner.scan_library(library_id)
    assert first.added == 1
    assert first.metadata_pending == 1
    assert first.aborted is False

    async with db_sessionmaker() as session:
        library = await session.get(Library, library_id)
        assert library is not None and library.last_scan_status == "ok"
        item = await session.scalar(select(MediaItem))
        assert item is not None
        assert item.match_state == "pending"
        assert item.match_error and "not configured" in item.match_error

    # The provider is not retried on an immediate rescan.
    await scanner.scan_library(library_id)
    assert provider.search_calls == 1


async def test_local_nfo_wins_over_remote(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "movies"
    folder = root / "The Matrix (1999)"
    folder.mkdir(parents=True)
    (folder / "The Matrix (1999).mkv").write_bytes(b"video")
    (folder / "movie.nfo").write_text(
        "<movie><title>The Matrix</title><year>1999</year><genre>Action</genre></movie>",
        encoding="utf-8",
    )
    library_id = await _make_library(db_sessionmaker, root)

    remote = FakeProvider(error=ProviderUnavailable("must not be called"))
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[LocalProvider(), remote])
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=FakeProbe(), matcher=matcher)

    stats = await scanner.scan_library(library_id)

    assert stats.matched == 1
    assert stats.provider_calls == 0
    assert remote.search_calls == 0
    async with db_sessionmaker() as session:
        item = await session.scalar(select(MediaItem))
        assert item is not None
        assert item.match_state == "matched"
        assert item.provider_ids["nfo"].endswith("movie.nfo")


async def test_edited_nfo_rematches_matched_item(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """An NFO edited after the first scan must be re-read on the next scan."""
    root = db_settings.media_dir / "movies"
    folder = root / "The Matrix (1999)"
    folder.mkdir(parents=True)
    (folder / "The Matrix (1999).mkv").write_bytes(b"video")
    nfo = folder / "movie.nfo"
    nfo.write_text("<movie><title>The Matrix</title><year>1999</year></movie>", encoding="utf-8")
    library_id = await _make_library(db_sessionmaker, root)

    # Use a clock ahead of real file mtimes so only the explicit mtime bump
    # below makes the sidecar look edited.
    current = datetime(2030, 1, 1, tzinfo=UTC)

    def clock() -> datetime:
        return current

    remote = FakeProvider(error=ProviderUnavailable("must not be called"))
    matcher = MetadataMatcher(
        db_settings, db_sessionmaker, providers=[LocalProvider(), remote], clock=clock
    )
    scanner = LibraryScanner(
        db_settings, db_sessionmaker, probe=FakeProbe(), matcher=matcher, clock=clock
    )

    first = await scanner.scan_library(library_id)
    assert first.matched == 1
    assert first.provider_calls == 0
    assert remote.search_calls == 0

    # Someone edits the NFO after the initial match.
    nfo.write_text(
        "<movie><title>The Matrix Reloaded</title><year>2003</year></movie>", encoding="utf-8"
    )
    bumped = (current + timedelta(hours=1)).timestamp()
    os.utime(nfo, (bumped, bumped))
    current = current + timedelta(hours=2)

    second = await scanner.scan_library(library_id)
    assert second.matched == 1
    assert second.provider_calls == 0
    assert remote.search_calls == 0

    async with db_sessionmaker() as session:
        item = await session.scalar(select(MediaItem))
        assert item is not None
        assert item.title == "The Matrix Reloaded"
        assert item.year == 2003
        assert item.match_state == "matched"

    # With no further edit, a later scan stays quiet.
    current = current + timedelta(hours=2)
    third = await scanner.scan_library(library_id)
    assert third.matched == 0
    assert third.provider_calls == 0


async def test_edited_tvshow_nfo_rematches_series(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """A series-level NFO edit is attributed to and re-read by the series."""
    root = db_settings.media_dir / "tv"
    series_dir = root / "Chernobyl (2019)"
    season_dir = series_dir / "Season 01"
    season_dir.mkdir(parents=True)
    (season_dir / "Chernobyl.S01E01.mkv").write_bytes(b"video")
    tv_nfo = series_dir / "tvshow.nfo"
    tv_nfo.write_text(
        "<tvshow><title>Chernobyl</title><year>2019</year></tvshow>", encoding="utf-8"
    )
    library_id = await _make_library(db_sessionmaker, root, library_type="tv")

    current = datetime(2030, 1, 1, tzinfo=UTC)

    def clock() -> datetime:
        return current

    remote = FakeProvider(error=ProviderUnavailable("must not be called"))
    matcher = MetadataMatcher(
        db_settings, db_sessionmaker, providers=[LocalProvider(), remote], clock=clock
    )
    scanner = LibraryScanner(
        db_settings, db_sessionmaker, probe=FakeProbe(), matcher=matcher, clock=clock
    )

    first = await scanner.scan_library(library_id)
    assert first.matched == 1
    assert remote.search_calls == 0

    tv_nfo.write_text(
        "<tvshow><title>Chernobyl: Extended</title><year>2019</year></tvshow>", encoding="utf-8"
    )
    bumped = (current + timedelta(hours=1)).timestamp()
    os.utime(tv_nfo, (bumped, bumped))
    current = current + timedelta(hours=2)

    second = await scanner.scan_library(library_id)
    assert second.matched == 1
    assert remote.search_calls == 0

    async with db_sessionmaker() as session:
        series_item = await session.scalar(select(MediaItem).where(MediaItem.type == "series"))
        assert series_item is not None
        assert series_item.title == "Chernobyl: Extended"
