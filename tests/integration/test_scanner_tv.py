"""Integration tests for TV show library scanning and matching."""

import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Episode,
    Library,
    LibraryPath,
    MediaFile,
    MediaItem,
    Season,
    User,
    UserItemState,
)
from app.services.media.probe import ProbeResult
from app.services.metadata.base import (
    EpisodeMetadata,
    MetadataProvider,
    PersonCredit,
    ProviderImage,
    SearchResult,
    SeasonMetadata,
    SeriesMetadata,
)
from app.services.metadata.matching import MetadataMatcher
from app.services.scanner.scanner import LibraryScanner


class FakeProbe:
    """Deterministic probe with a call counter."""

    def __init__(self) -> None:
        self.calls: list[Path] = []

    async def __call__(self, path: Path) -> ProbeResult:
        self.calls.append(path)
        return ProbeResult(
            container="matroska,webm",
            duration_seconds=3000.0,
            bit_rate=5_000_000,
            streams=[],
        )


class MockTVProvider(MetadataProvider):
    name = "tmdb"

    def __init__(self) -> None:
        self.searches: list[str] = []

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        return []

    async def fetch_details(self, provider_id: str) -> None:
        return None

    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        return []

    async def search_tv(self, query: str, year: int | None = None) -> list[SearchResult]:
        self.searches.append(query)
        if "breaking bad" in query.casefold():
            return [
                SearchResult(
                    provider=self.name,
                    provider_id="1396",
                    title="Breaking Bad",
                    year=2008,
                    overview="A chemistry teacher diagnosed with cancer...",
                    popularity=100.0,
                )
            ]
        if "better call saul" in query.casefold():
            return [
                SearchResult(
                    provider=self.name,
                    provider_id="60059",
                    title="Better Call Saul",
                    year=2015,
                    overview="The trials and tribulations of criminal lawyer Jimmy McGill.",
                    popularity=80.0,
                )
            ]
        return []

    async def fetch_tv_details(self, series_id: str) -> SeriesMetadata | None:
        if series_id == "1396":
            return SeriesMetadata(
                provider=self.name,
                provider_id="1396",
                title="Breaking Bad",
                year=2008,
                first_air_date="2008-01-20",
                status="Ended",
                network="AMC",
                rating=8.9,
                overview="A chemistry teacher turned meth kingpin.",
                genres=("Drama", "Crime"),
                credits=(
                    PersonCredit(name="Bryan Cranston", role="cast", character="Walter White"),
                ),
                provider_ids={"tmdb": "1396"},
            )
        if series_id == "60059":
            return SeriesMetadata(
                provider=self.name,
                provider_id="60059",
                title="Better Call Saul",
                year=2015,
                first_air_date="2015-02-08",
                status="Ended",
                network="AMC",
                rating=8.7,
                overview="Jimmy McGill before Saul Goodman.",
                genres=("Drama", "Crime"),
                credits=(PersonCredit(name="Bob Odenkirk", role="cast", character="Jimmy McGill"),),
                provider_ids={"tmdb": "60059"},
            )
        return None

    async def fetch_tv_season(self, series_id: str, season_number: int) -> SeasonMetadata | None:
        if series_id == "1396" and season_number == 1:
            return SeasonMetadata(
                provider=self.name,
                provider_id="3572",
                series_provider_id="1396",
                season_number=1,
                title="Season 1",
                episodes=(
                    EpisodeMetadata(
                        provider=self.name,
                        provider_id="62085",
                        series_provider_id="1396",
                        season_number=1,
                        episode_number=1,
                        title="Pilot",
                        overview="Walter White discovers he has lung cancer.",
                        runtime_seconds=58 * 60,
                        rating=8.4,
                    ),
                    EpisodeMetadata(
                        provider=self.name,
                        provider_id="62086",
                        series_provider_id="1396",
                        season_number=1,
                        episode_number=2,
                        title="Cat's in the Bag...",
                        overview="Walt and Jesse dispose of bodies.",
                        runtime_seconds=48 * 60,
                        rating=8.3,
                    ),
                ),
            )
        if series_id == "60059" and season_number == 1:
            return SeasonMetadata(
                provider=self.name,
                provider_id="60001",
                series_provider_id="60059",
                season_number=1,
                title="Season 1",
                episodes=(
                    EpisodeMetadata(
                        provider=self.name,
                        provider_id="90001",
                        series_provider_id="60059",
                        season_number=1,
                        episode_number=1,
                        title="Uno",
                        overview="Jimmy McGill is a struggling lawyer.",
                        runtime_seconds=53 * 60,
                        rating=8.5,
                    ),
                ),
            )
        return None

    async def fetch_tv_images(self, series_id: str) -> list[ProviderImage]:
        return [
            ProviderImage(
                kind="poster",
                source_url="https://image.tmdb.org/poster.jpg",
                is_primary=True,
            ),
            ProviderImage(
                kind="backdrop",
                source_url="https://image.tmdb.org/backdrop.jpg",
                is_primary=True,
            ),
        ]


async def _make_tv_library(
    sessionmaker: async_sessionmaker[AsyncSession], root: Path, name: str | None = None
) -> str:
    async with sessionmaker() as session:
        library = Library(name=name or f"TV Shows {uuid.uuid4().hex[:8]}", type="tv")
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.commit()
        return library.id


def _write_tv_file(root: Path, relative_path: str, content: bytes = b"not-a-real-video") -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


async def test_tv_scanner_adds_series_seasons_episodes_and_rescan_unchanged(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "tv"
    root.mkdir(parents=True, exist_ok=True)
    _write_tv_file(root, "Breaking Bad/Season 01/S01E01 - Pilot.mkv")
    _write_tv_file(root, "Breaking Bad/Season 01/S01E02 - Cat's in the Bag.mkv")
    _write_tv_file(root, "Breaking Bad/Season 02/S02E01 - Seven Thirty-Seven.mkv")

    library_id = await _make_tv_library(db_sessionmaker, root)
    probe = FakeProbe()
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=probe)

    first = await scanner.scan_library(library_id)
    # 3 files added. Items added: 1 series + 2 seasons + 3 episodes = 6 items
    assert first.added == 3
    assert first.added_items == 6
    assert first.probes == 3
    assert first.unchanged == 0

    # Rescan does 0 probes and marks all 3 unchanged
    second = await scanner.scan_library(library_id)
    assert second.unchanged == 3
    assert second.probes == 0
    assert len(probe.calls) == 3

    async with db_sessionmaker() as session:
        # Check series
        series_items = list(
            await session.scalars(select(MediaItem).where(MediaItem.type == "series"))
        )
        assert len(series_items) == 1
        assert series_items[0].title == "Breaking Bad"

        # Check seasons
        season_items = list(await session.scalars(select(Season).order_by(Season.season_number)))
        assert len(season_items) == 2
        assert season_items[0].season_number == 1
        assert season_items[1].season_number == 2
        assert season_items[0].series_id == series_items[0].id

        # Check episodes
        episodes = list(
            await session.scalars(
                select(Episode).order_by(Episode.season_number, Episode.episode_number)
            )
        )
        assert len(episodes) == 3
        assert episodes[0].season_number == 1 and episodes[0].episode_number == 1
        assert episodes[1].season_number == 1 and episodes[1].episode_number == 2
        assert episodes[2].season_number == 2 and episodes[2].episode_number == 1
        assert all(ep.file_id is not None for ep in episodes)


async def test_tv_scanner_handles_multi_episode_specials_date_based_and_unknown(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "tv2"
    root.mkdir(parents=True, exist_ok=True)
    multi = _write_tv_file(root, "Fringe/Season 1/S01E01E02.mkv")
    special = _write_tv_file(root, "Fringe/Specials/SP01.mkv")
    date_ep = _write_tv_file(root, "The Daily Show/2024/2024-03-01.mkv")
    unknown = _write_tv_file(root, "Fringe/Random Extra.mkv")

    library_id = await _make_tv_library(db_sessionmaker, root)
    probe = FakeProbe()
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=probe)

    stats = await scanner.scan_library(library_id)
    assert stats.added == 4

    async with db_sessionmaker() as session:
        # 1. Multi-episode file: two Episode rows share the same MediaFile
        multi_file = await session.scalar(select(MediaFile).where(MediaFile.path == str(multi)))
        assert multi_file is not None
        linked_eps = list(
            await session.scalars(
                select(Episode)
                .where(Episode.file_id == multi_file.id)
                .order_by(Episode.episode_number)
            )
        )
        assert len(linked_eps) == 2
        assert linked_eps[0].episode_number == 1
        assert linked_eps[1].episode_number == 2

        # 2. Specials file: season 0
        spec_ep = await session.scalar(
            select(Episode)
            .join(MediaFile, Episode.file_id == MediaFile.id)
            .where(MediaFile.path == str(special))
        )
        assert spec_ep is not None
        assert spec_ep.season_number == 0
        assert spec_ep.episode_number == 1

        # 3. Date-based file: air_date set
        date_item = await session.scalar(
            select(Episode)
            .join(MediaFile, Episode.file_id == MediaFile.id)
            .where(MediaFile.path == str(date_ep))
        )
        assert date_item is not None
        assert date_item.air_date == "2024-03-01"

        # 4. Unknown episode: season_id=None, season_number=None, episode_number=None
        unk_ep = await session.scalar(
            select(Episode)
            .join(MediaFile, Episode.file_id == MediaFile.id)
            .where(MediaFile.path == str(unknown))
        )
        assert unk_ep is not None
        assert unk_ep.season_id is None
        assert unk_ep.season_number is None
        assert unk_ep.episode_number is None
        assert unk_ep.item.match_state == "unmatched"


async def test_tv_scanner_matching_with_mock_tmdb(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "tv3"
    root.mkdir(parents=True, exist_ok=True)
    _write_tv_file(root, "Breaking Bad/Season 01/S01E01.mkv")
    _write_tv_file(root, "Breaking Bad/Season 01/S01E02.mkv")
    _write_tv_file(root, "Breaking Bad/Random Clip.mkv")

    library_id = await _make_tv_library(db_sessionmaker, root)
    probe = FakeProbe()
    provider = MockTVProvider()
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[provider])
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=probe, matcher=matcher)

    stats = await scanner.scan_library(library_id)
    assert stats.matched >= 1

    async with db_sessionmaker() as session:
        # Series matched
        series = await session.scalar(select(MediaItem).where(MediaItem.type == "series"))
        assert series is not None
        assert series.match_state == "matched"
        assert series.overview == "A chemistry teacher turned meth kingpin."
        assert series.series.network == "AMC"

        # Season 1 matched
        season = await session.scalar(select(MediaItem).where(MediaItem.type == "season"))
        assert season is not None
        assert season.title == "Season 1"

        # Episode 1 and 2 matched
        ep1 = await session.scalar(
            select(Episode).where(Episode.season_number == 1, Episode.episode_number == 1)
        )
        assert ep1 is not None
        assert ep1.item.title == "Pilot"
        assert ep1.item.match_state == "matched"
        assert ep1.item.runtime_seconds == 58 * 60

        ep2 = await session.scalar(
            select(Episode).where(Episode.season_number == 1, Episode.episode_number == 2)
        )
        assert ep2 is not None
        assert ep2.item.title == "Cat's in the Bag..."
        assert ep2.item.match_state == "matched"

        # Unknown clip remains unmatched under the series
        unknown = await session.scalar(select(Episode).where(Episode.season_number.is_(None)))
        assert unknown is not None
        assert unknown.item.match_state == "unmatched"


async def test_tv_scanner_move_file_preserves_episode(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "tv4"
    root.mkdir(parents=True, exist_ok=True)
    old_file = _write_tv_file(
        root, "Breaking Bad/Season 01/S01E01.mkv", b"same-unique-bytes-for-hash"
    )

    library_id = await _make_tv_library(db_sessionmaker, root)
    probe = FakeProbe()
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=probe)

    first = await scanner.scan_library(library_id)
    assert first.added == 1

    async with db_sessionmaker() as session:
        ep_before = await session.scalar(select(Episode))
        assert ep_before is not None
        item_id_before = ep_before.item_id
        file_id_before = ep_before.file_id

    # Move file to a different name
    new_file = root / "Breaking Bad/Season 01/Breaking Bad - 1x01 - Pilot.mkv"
    old_file.rename(new_file)

    second = await scanner.scan_library(library_id)
    assert second.moved == 1

    async with db_sessionmaker() as session:
        ep_after = await session.scalar(select(Episode))
        assert ep_after is not None
        assert ep_after.item_id == item_id_before
        assert ep_after.file_id == file_id_before
        assert ep_after.file.path == str(new_file)


async def test_tv_manual_rematch_series_preserves_watch_state(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "tv5"
    root.mkdir(parents=True, exist_ok=True)
    _write_tv_file(root, "Breaking Bad/Season 01/S01E01.mkv")

    library_id = await _make_tv_library(db_sessionmaker, root)
    probe = FakeProbe()
    provider = MockTVProvider()
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[provider])
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=probe, matcher=matcher)

    await scanner.scan_library(library_id)

    # Seed user watch progress on S01E01
    async with db_sessionmaker() as session:
        user = User(username="tvwatcher", password_hash="dummy")
        session.add(user)
        await session.flush()

        ep1 = await session.scalar(
            select(Episode).where(Episode.season_number == 1, Episode.episode_number == 1)
        )
        assert ep1 is not None
        session.add(
            UserItemState(
                user_id=user.id,
                item_id=ep1.item_id,
                played=True,
                position_ms=123456,
            )
        )
        series = await session.scalar(select(MediaItem).where(MediaItem.type == "series"))
        assert series is not None
        series_id = series.id
        await session.commit()

    # Manually re-match the series to Better Call Saul (id="60059")
    result = await matcher.manual_match(series_id, provider_name="tmdb", provider_id="60059")
    assert result.state == "matched"

    # Verify series updated to Better Call Saul
    async with db_sessionmaker() as session:
        series_updated = await session.get(MediaItem, series_id)
        assert series_updated is not None
        assert series_updated.title == "Better Call Saul"

        # S01E01 updated to "Uno"
        ep1_updated = await session.scalar(
            select(Episode).where(Episode.season_number == 1, Episode.episode_number == 1)
        )
        assert ep1_updated is not None
        assert ep1_updated.item.title == "Uno"

        # Watch state preserved!
        state = await session.scalar(
            select(UserItemState).where(UserItemState.item_id == ep1_updated.item_id)
        )
        assert state is not None
        assert state.played is True
        assert state.position_ms == 123456
