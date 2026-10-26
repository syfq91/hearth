"""Phase 4 acceptance tests verifying all three acceptance criteria.

Criteria:
1. A fixture tree with 3 series covering normal, multi-episode, date-based and specials
   files matches 100% correctly.
2. Next-up behaves correctly across season boundaries and with out-of-order watching.
3. Manual re-match of a series moves all episodes and preserves watch states where
   episode numbers still exist.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Episode,
    Library,
    LibraryPath,
    MediaFile,
    MediaItem,
    Season,
    Series,
)
from app.services.media.probe import ProbeResult
from app.services.media.titles import sort_title
from app.services.metadata.base import (
    EpisodeMetadata,
    MetadataProvider,
    ProviderImage,
    SearchResult,
    SeasonMetadata,
    SeriesMetadata,
)
from app.services.metadata.matching import MetadataMatcher
from app.services.scanner.scanner import LibraryScanner

ADMIN = {"username": "admin", "password": "correct horse"}
NOW = datetime.now(UTC)


class AcceptanceStubProbe:
    """Fast probe returning valid container and streams for acceptance tests."""

    async def __call__(self, path: Path) -> ProbeResult:
        return ProbeResult(
            container="matroska,webm",
            duration_seconds=3600.0,
            bit_rate=5_000_000,
            streams=[],
        )


def _ep_meta(
    series_provider_id: str,
    season_number: int,
    episode_number: int,
    title: str,
    air_date: str | None = None,
    overview: str | None = None,
    runtime_seconds: int = 3600,
) -> EpisodeMetadata:
    return EpisodeMetadata(
        provider="tmdb",
        provider_id=f"{series_provider_id}s{season_number}e{episode_number}",
        series_provider_id=series_provider_id,
        season_number=season_number,
        episode_number=episode_number,
        title=title,
        overview=overview,
        air_date=air_date,
        runtime_seconds=runtime_seconds,
    )


class AcceptanceMockProvider(MetadataProvider):
    """Scripted metadata provider covering 3 series for acceptance testing."""

    name = "tmdb"

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        return []

    async def fetch_details(self, provider_id: str) -> None:
        return None

    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        return []

    async def search_tv(self, query: str, year: int | None = None) -> list[SearchResult]:
        q = query.casefold()
        if "wire" in q:
            return [
                SearchResult(
                    provider=self.name,
                    provider_id="1438",
                    title="The Wire",
                    year=2002,
                    overview="The Baltimore drug scene, seen through law enforcement eyes...",
                    popularity=95.0,
                )
            ]
        if "battlestar" in q:
            return [
                SearchResult(
                    provider=self.name,
                    provider_id="1972",
                    title="Battlestar Galactica",
                    year=2003,
                    overview="When an old enemy, the Cylons, resurface...",
                    popularity=90.0,
                )
            ]
        if "daily show" in q:
            return [
                SearchResult(
                    provider=self.name,
                    provider_id="2224",
                    title="The Daily Show",
                    year=1996,
                    overview="The comedic news show giving satirical views on current events...",
                    popularity=85.0,
                )
            ]
        if "planet earth ii" in q or query == "1002":
            return [
                SearchResult(
                    provider=self.name,
                    provider_id="1002",
                    title="Planet Earth II",
                    year=2016,
                    overview="Wildlife documentary series exploring iconic habitats...",
                    popularity=98.0,
                )
            ]
        if "planet earth" in q or query == "1001":
            return [
                SearchResult(
                    provider=self.name,
                    provider_id="1001",
                    title="Planet Earth",
                    year=2006,
                    overview="David Attenborough celebrates the natural world...",
                    popularity=92.0,
                )
            ]
        return []

    async def fetch_tv_details(self, series_id: str) -> SeriesMetadata | None:
        if series_id == "1438":
            return SeriesMetadata(
                provider=self.name,
                provider_id="1438",
                title="The Wire",
                year=2002,
                first_air_date="2002-06-02",
                status="Ended",
                network="HBO",
                overview="The Baltimore drug scene, seen through the eyes of dealers and cops.",
                genres=("Drama", "Crime"),
            )
        if series_id == "1972":
            return SeriesMetadata(
                provider=self.name,
                provider_id="1972",
                title="Battlestar Galactica",
                year=2003,
                first_air_date="2003-12-08",
                status="Ended",
                network="Sci-Fi Channel",
                overview="When an old enemy resurfaces and obliterates the 12 colonies...",
                genres=("Sci-Fi & Fantasy", "Action & Adventure", "Drama"),
            )
        if series_id == "2224":
            return SeriesMetadata(
                provider=self.name,
                provider_id="2224",
                title="The Daily Show",
                year=1996,
                first_air_date="1996-07-22",
                status="Returning Series",
                network="Comedy Central",
                overview="The Daily Show is an American news satire and talk show.",
                genres=("Comedy", "News", "Talk"),
            )
        if series_id == "1001":
            return SeriesMetadata(
                provider=self.name,
                provider_id="1001",
                title="Planet Earth",
                year=2006,
                first_air_date="2006-03-05",
                status="Ended",
                network="BBC One",
                overview="David Attenborough celebrates the amazing variety of the natural world.",
                genres=("Documentary",),
            )
        if series_id == "1002":
            return SeriesMetadata(
                provider=self.name,
                provider_id="1002",
                title="Planet Earth II",
                year=2016,
                first_air_date="2016-11-06",
                status="Ended",
                network="BBC One",
                overview="Wildlife documentary series exploring the world's most iconic habitats.",
                genres=("Documentary",),
            )
        return None

    async def fetch_tv_season(self, series_id: str, season_number: int) -> SeasonMetadata | None:
        if series_id == "1438" and season_number == 1:
            return SeasonMetadata(
                provider=self.name,
                series_provider_id="1438",
                season_number=1,
                title="Season 1",
                overview="The first season of The Wire introduces Baltimore's narcotics trade.",
                episodes=(
                    _ep_meta(
                        "1438",
                        1,
                        1,
                        "The Target",
                        "2002-06-02",
                        "Detective Jimmy McNulty investigates D'Angelo Barksdale.",
                    ),
                    _ep_meta(
                        "1438",
                        1,
                        2,
                        "The Detail",
                        "2002-06-09",
                        "A joint narcotics detail is formed.",
                    ),
                    _ep_meta(
                        "1438",
                        1,
                        3,
                        "The Buys",
                        "2002-06-16",
                        "The detail executes hand-to-hand drug buys.",
                    ),
                    _ep_meta(
                        "1438",
                        1,
                        4,
                        "Old Cases",
                        "2002-06-23",
                        "Greggs and McNulty look into old Barksdale hits.",
                    ),
                ),
            )
        if series_id == "1972":
            if season_number == 0:
                return SeasonMetadata(
                    provider=self.name,
                    series_provider_id="1972",
                    season_number=0,
                    title="Specials",
                    overview="Miniseries and special broadcasts.",
                    episodes=(
                        _ep_meta(
                            "1972",
                            0,
                            1,
                            "The Miniseries (Part 1)",
                            "2003-12-08",
                            "The Cylons launch a surprise nuclear attack.",
                            runtime_seconds=5400,
                        ),
                    ),
                )
            if season_number == 1:
                return SeasonMetadata(
                    provider=self.name,
                    series_provider_id="1972",
                    season_number=1,
                    title="Season 1",
                    overview="The survivors escape the Cylon fleet.",
                    episodes=(
                        _ep_meta(
                            "1972",
                            1,
                            1,
                            "33",
                            "2004-10-18",
                            "The fleet jumps every 33 minutes to evade Cylons.",
                            runtime_seconds=2640,
                        ),
                    ),
                )
        if series_id == "2224" and season_number == 2024:
            return SeasonMetadata(
                provider=self.name,
                series_provider_id="2224",
                season_number=2024,
                title="Season 2024",
                overview="Episodes aired during the year 2024.",
                episodes=(
                    _ep_meta(
                        "2224",
                        2024,
                        1,
                        "Jon Stewart - March 1, 2024",
                        "2024-03-01",
                        "Jon Stewart hosts coverage of current events.",
                        runtime_seconds=1800,
                    ),
                    _ep_meta(
                        "2224",
                        2024,
                        2,
                        "Jon Stewart - March 2, 2024",
                        "2024-03-02",
                        "Jon Stewart returns to discuss global politics.",
                        runtime_seconds=1800,
                    ),
                ),
            )
        if series_id == "1001" and season_number == 1:
            return SeasonMetadata(
                provider=self.name,
                series_provider_id="1001",
                season_number=1,
                title="Season 1",
                episodes=(
                    _ep_meta(
                        "1001",
                        1,
                        1,
                        "From Pole to Pole",
                        overview="A journey from the Arctic to the Antarctic.",
                    ),
                    _ep_meta("1001", 1, 2, "Mountains", overview="The high places of our world."),
                    _ep_meta(
                        "1001",
                        1,
                        3,
                        "Fresh Water",
                        overview="The rivers and lakes of planet Earth.",
                    ),
                ),
            )
        if series_id == "1002" and season_number == 1:
            return SeasonMetadata(
                provider=self.name,
                series_provider_id="1002",
                season_number=1,
                title="Season 1",
                episodes=(
                    _ep_meta(
                        "1002",
                        1,
                        1,
                        "Islands",
                        overview="Remote islands offer sanctuary to strange creatures.",
                    ),
                    _ep_meta("1002", 1, 2, "Mountains", overview="The treacherous mountain world."),
                    _ep_meta(
                        "1002", 1, 3, "Jungles", overview="The lush depths of the rainforest."
                    ),
                ),
            )
        return None

    async def fetch_tv_episode(
        self, series_id: str, season_number: int, episode_number: int
    ) -> EpisodeMetadata | None:
        season = await self.fetch_tv_season(series_id, season_number)
        if season is not None:
            for ep in season.episodes:
                if ep.episode_number == episode_number:
                    return ep
        return None


def _write_media_file(path: Path, content: bytes = b"fake-media-bytes") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


async def _make_tv_library(
    sessionmaker: async_sessionmaker[AsyncSession], root: Path, name: str = "TV Shows"
) -> str:
    async with sessionmaker() as session:
        library = Library(name=name, type="tv")
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.commit()
        return library.id


# ==============================================================================
# Criterion 1: A fixture tree with 3 series covering normal, multi-episode,
# date-based and specials files matches 100% correctly.
# ==============================================================================
async def test_phase4_acceptance_criterion_1_fixture_tree_matches_100_percent(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    root = db_settings.media_dir / "tv_acceptance"
    root.mkdir(parents=True, exist_ok=True)

    # 1. Normal files + Multi-episode file
    # The Wire Season 1: E01 (normal), E02 (normal), E03-E04 (multi-episode)
    _write_media_file(root / "The Wire/Season 01/The Wire - S01E01 - The Target.mkv")
    _write_media_file(root / "The Wire/Season 01/The Wire - S01E02 - The Detail.mkv")
    wire_multi = _write_media_file(root / "The Wire/Season 01/The Wire - S01E03E04.mkv")

    # 2. Specials file + Normal file
    # Battlestar Galactica (2003): S00E01 (special in Specials folder), S01E01 (normal)
    _write_media_file(
        root
        / "Battlestar Galactica (2003)"
        / "Specials"
        / "Battlestar Galactica - S00E01 - The Miniseries Part 1.mkv"
    )
    _write_media_file(
        root / "Battlestar Galactica (2003)/Season 01/Battlestar Galactica - S01E01 - 33.mkv"
    )

    # 3. Date-based daily show files
    # The Daily Show: 2024-03-01 and 2024-03-02
    _write_media_file(root / "The Daily Show/Season 2024/The Daily Show - 2024-03-01.mkv")
    _write_media_file(root / "The Daily Show/Season 2024/The Daily Show - 2024-03-02.mkv")

    library_id = await _make_tv_library(db_sessionmaker, root)
    provider = AcceptanceMockProvider()
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[provider])
    probe = AcceptanceStubProbe()
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=probe, matcher=matcher)

    stats = await scanner.scan_library(library_id)
    assert stats.added == 7
    assert stats.errors == 0
    assert stats.unmatched == 0

    async with db_sessionmaker() as session:
        # Assertion A: Exactly 3 series created, all matched 100%
        series_items = list(
            await session.scalars(
                select(MediaItem).where(MediaItem.type == "series").order_by(MediaItem.title)
            )
        )
        assert len(series_items) == 3
        series_by_title = {s.title: s for s in series_items}
        assert set(series_by_title.keys()) == {
            "The Wire",
            "Battlestar Galactica",
            "The Daily Show",
        }
        for s in series_items:
            assert s.match_state == "matched", f"Series {s.title} was not matched: {s.match_state}"

        # Assertion B: The Wire matching details (normal + multi-episode)
        wire_id = series_by_title["The Wire"].id
        wire_eps = list(
            await session.scalars(
                select(Episode)
                .where(Episode.series_id == wire_id)
                .order_by(Episode.season_number, Episode.episode_number)
            )
        )
        assert len(wire_eps) == 4
        assert [e.episode_number for e in wire_eps] == [1, 2, 3, 4]
        assert [e.item.title for e in wire_eps] == [
            "The Target",
            "The Detail",
            "The Buys",
            "Old Cases",
        ]
        for ep in wire_eps:
            assert ep.item.match_state == "matched"
        # Multi-episode check: E03 and E04 share the same MediaFile
        wire_multi_file = await session.scalar(
            select(MediaFile).where(MediaFile.path == str(wire_multi))
        )
        assert wire_multi_file is not None
        assert wire_eps[2].file_id == wire_multi_file.id
        assert wire_eps[3].file_id == wire_multi_file.id
        # Primary episode owns media_files.item_id
        assert wire_multi_file.item_id == wire_eps[2].item_id

        # Assertion C: Battlestar Galactica matching details (specials + normal)
        bsg_id = series_by_title["Battlestar Galactica"].id
        bsg_eps = list(
            await session.scalars(
                select(Episode)
                .where(Episode.series_id == bsg_id)
                .order_by(Episode.season_number, Episode.episode_number)
            )
        )
        assert len(bsg_eps) == 2
        # Special episode: Season 0, Episode 1
        assert bsg_eps[0].season_number == 0
        assert bsg_eps[0].episode_number == 1
        assert bsg_eps[0].item.title == "The Miniseries (Part 1)"
        assert bsg_eps[0].item.match_state == "matched"
        # Normal episode: Season 1, Episode 1
        assert bsg_eps[1].season_number == 1
        assert bsg_eps[1].episode_number == 1
        assert bsg_eps[1].item.title == "33"
        assert bsg_eps[1].item.match_state == "matched"

        # Assertion D: The Daily Show matching details (date-based)
        daily_id = series_by_title["The Daily Show"].id
        daily_eps = list(
            await session.scalars(
                select(Episode)
                .where(Episode.series_id == daily_id)
                .order_by(Episode.season_number, Episode.episode_number)
            )
        )
        assert len(daily_eps) == 2
        assert daily_eps[0].air_date == "2024-03-01"
        assert daily_eps[0].item.title == "Jon Stewart - March 1, 2024"
        assert daily_eps[0].item.match_state == "matched"
        assert daily_eps[1].air_date == "2024-03-02"
        assert daily_eps[1].item.title == "Jon Stewart - March 2, 2024"
        assert daily_eps[1].item.match_state == "matched"

        # Assertion E: 100% MATCHED - ZERO unmatched or pending items across the whole library
        unmatched_count = await session.scalar(
            select(func.count())
            .select_from(MediaItem)
            .where(MediaItem.library_id == library_id, MediaItem.match_state != "matched")
        )
        assert unmatched_count == 0, (
            f"Expected 100% matched items, but found {unmatched_count} unmatched items"
        )


# ==============================================================================
# Criterion 2: Next-up behaves correctly across season boundaries and with
# out-of-order watching.
# ==============================================================================
async def test_phase4_acceptance_criterion_2_next_up_season_boundaries_and_out_of_order(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # 1. Setup admin and library
    setup_resp = await api_client.post("/api/v1/auth/setup", json=ADMIN)
    assert setup_resp.status_code == 201
    auth_header = {"Authorization": f"Bearer {setup_resp.json()['access_token']}"}

    root = db_settings.media_dir / "tv_next_up"
    root.mkdir(parents=True, exist_ok=True)
    library_id = await _make_tv_library(db_sessionmaker, root, name="Next Up TV")

    # 2. Seed a series with Season 1 (E1, E2, E3) and Season 2 (E1, E2, E3)
    ep_map: dict[str, str] = {}
    series_id: str = ""
    async with db_sessionmaker() as session:
        series_item = MediaItem(
            library_id=library_id,
            type="series",
            title="The Next Up Odyssey",
            sort_title=sort_title("The Next Up Odyssey"),
            year=2024,
            match_state="matched",
        )
        session.add(series_item)
        await session.flush()
        series_id = series_item.id
        session.add(
            Series(
                item_id=series_id,
                original_title="The Next Up Odyssey",
                status="Returning Series",
                network="HBO",
            )
        )

        for s_num in (1, 2):
            s_item = MediaItem(
                library_id=library_id,
                type="season",
                title=f"Season {s_num}",
                sort_title=sort_title(f"Season {s_num}"),
                match_state="matched",
            )
            session.add(s_item)
            await session.flush()
            session.add(Season(item_id=s_item.id, series_id=series_id, season_number=s_num))

            for e_num in (1, 2, 3):
                e_item = MediaItem(
                    library_id=library_id,
                    type="episode",
                    title=f"S{s_num:02d}E{e_num:02d}",
                    sort_title=sort_title(f"S{s_num:02d}E{e_num:02d}"),
                    runtime_seconds=2700,
                    match_state="matched",
                )
                session.add(e_item)
                await session.flush()
                session.add(
                    Episode(
                        item_id=e_item.id,
                        series_id=series_id,
                        season_id=s_item.id,
                        season_number=s_num,
                        episode_number=e_num,
                    )
                )
                ep_map[f"S{s_num:02d}E{e_num:02d}"] = e_item.id

        await session.commit()

    # Step A: Unwatched series -> Next-up must be S01E01
    resp = await api_client.get(f"/api/v1/series/{series_id}/next-up", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["id"] == ep_map["S01E01"]
    assert resp.json()["season_number"] == 1
    assert resp.json()["episode_number"] == 1

    # Step B: Watch S01E01 and S01E02 sequentially -> Next-up must advance to S01E03
    p1 = await api_client.post(
        f"/api/v1/items/{ep_map['S01E01']}/watched",
        json={"watched": True},
        headers=auth_header,
    )
    assert p1.status_code == 200

    resp = await api_client.get(f"/api/v1/series/{series_id}/next-up", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["id"] == ep_map["S01E02"]

    p2 = await api_client.post(
        f"/api/v1/items/{ep_map['S01E02']}/watched",
        json={"watched": True},
        headers=auth_header,
    )
    assert p2.status_code == 200

    resp = await api_client.get(f"/api/v1/series/{series_id}/next-up", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["id"] == ep_map["S01E03"]

    # Step C: Finish Season 1 (S01E03) -> Season boundary crossed! Next-up must be S02E01
    p3 = await api_client.post(
        f"/api/v1/items/{ep_map['S01E03']}/watched",
        json={"watched": True},
        headers=auth_header,
    )
    assert p3.status_code == 200

    resp = await api_client.get(f"/api/v1/series/{series_id}/next-up", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["id"] == ep_map["S02E01"]
    assert resp.json()["season_number"] == 2
    assert resp.json()["episode_number"] == 1

    # Step D: Out-of-order watching: User skips S02E01 and watches S02E02 directly
    # Next-up should advance to S02E03 (first unwatched after most recently watched)
    p_skip = await api_client.post(
        f"/api/v1/items/{ep_map['S02E02']}/watched",
        json={"watched": True},
        headers=auth_header,
    )
    assert p_skip.status_code == 200

    resp = await api_client.get(f"/api/v1/series/{series_id}/next-up", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["id"] == ep_map["S02E03"]

    # Step E: Out-of-order watching: User watches S02E03 (the finale).
    # All trailing episodes are now watched, but S02E01 was skipped earlier.
    # Next-up must fall back to the earliest skipped episode in the show: S02E01!
    p_fin = await api_client.post(
        f"/api/v1/items/{ep_map['S02E03']}/watched",
        json={"watched": True},
        headers=auth_header,
    )
    assert p_fin.status_code == 200

    resp = await api_client.get(f"/api/v1/series/{series_id}/next-up", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["id"] == ep_map["S02E01"]
    assert resp.json()["season_number"] == 2
    assert resp.json()["episode_number"] == 1

    # Step F: Out-of-order watching: User un-marks S01E02 and re-watches S01E01.
    # Reporting playback on S01E01 makes it the most recent last_played_at.
    # Next-up should point to S01E02!
    await api_client.post(
        f"/api/v1/items/{ep_map['S01E02']}/watched",
        json={"watched": False},
        headers=auth_header,
    )
    p_rewatch = await api_client.post(
        f"/api/v1/items/{ep_map['S01E01']}/progress",
        json={"position_ms": 100000, "state": "playing"},
        headers=auth_header,
    )
    assert p_rewatch.status_code == 200

    resp = await api_client.get(f"/api/v1/series/{series_id}/next-up", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["id"] == ep_map["S01E02"]

    # Step G: Mark all watched on the series -> Next-up returns 404
    p_all = await api_client.post(
        f"/api/v1/series/{series_id}/watched",
        json={"watched": True},
        headers=auth_header,
    )
    assert p_all.status_code == 200

    resp = await api_client.get(f"/api/v1/series/{series_id}/next-up", headers=auth_header)
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "next_up_not_found"

    # Shelf GET /shows/next-up returns empty for this fully-watched show
    shelf = await api_client.get("/api/v1/shows/next-up", headers=auth_header)
    assert shelf.status_code == 200
    assert all(item["series_id"] != series_id for item in shelf.json()["items"])


# ==============================================================================
# Criterion 3: Manual re-match of a series moves all episodes and preserves
# watch states where episode numbers still exist.
# ==============================================================================
async def test_phase4_acceptance_criterion_3_manual_rematch_preserves_watch_states(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    # 1. Setup admin and library
    setup_resp = await api_client.post("/api/v1/auth/setup", json=ADMIN)
    assert setup_resp.status_code == 201
    admin_auth = {"Authorization": f"Bearer {setup_resp.json()['access_token']}"}

    root = db_settings.media_dir / "tv_rematch"
    root.mkdir(parents=True, exist_ok=True)
    _write_media_file(root / "Planet Earth/Season 1/Planet Earth - S01E01.mkv")
    _write_media_file(root / "Planet Earth/Season 1/Planet Earth - S01E02.mkv")
    _write_media_file(root / "Planet Earth/Season 1/Planet Earth - S01E03.mkv")

    library_id = await _make_tv_library(db_sessionmaker, root, name="Docu TV")
    provider = AcceptanceMockProvider()
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[provider])
    probe = AcceptanceStubProbe()
    scanner = LibraryScanner(db_settings, db_sessionmaker, probe=probe, matcher=matcher)

    await scanner.scan_library(library_id)

    # 2. Record episode item IDs and record watch states
    ep1_id: str = ""
    ep2_id: str = ""
    ep3_id: str = ""
    series_id: str = ""

    async with db_sessionmaker() as session:
        series_item = await session.scalar(select(MediaItem).where(MediaItem.type == "series"))
        assert series_item is not None
        assert series_item.title == "Planet Earth"
        series_id = series_item.id

        episodes = list(
            await session.scalars(
                select(Episode)
                .where(Episode.series_id == series_id)
                .order_by(Episode.episode_number)
            )
        )
        assert len(episodes) == 3
        ep1_id = episodes[0].item_id
        ep2_id = episodes[1].item_id
        ep3_id = episodes[2].item_id

        assert episodes[0].item.title == "From Pole to Pole"
        assert episodes[1].item.title == "Mountains"
        assert episodes[2].item.title == "Fresh Water"

    # User sets watch states on episodes:
    # E01: watched (played=True, position_ms=3000000)
    w1 = await api_client.post(
        f"/api/v1/items/{ep1_id}/watched",
        json={"watched": True},
        headers=admin_auth,
    )
    assert w1.status_code == 200
    p1 = await api_client.post(
        f"/api/v1/items/{ep1_id}/progress",
        json={"position_ms": 3000000, "state": "paused"},
        headers=admin_auth,
    )
    assert p1.status_code == 200

    # E02: midway through (played=False, position_ms=1500000, favorite=True, rating=9)
    p2 = await api_client.post(
        f"/api/v1/items/{ep2_id}/progress",
        json={"position_ms": 1500000, "state": "paused"},
        headers=admin_auth,
    )
    assert p2.status_code == 200
    fav2 = await api_client.post(
        f"/api/v1/items/{ep2_id}/favorite",
        json={"favorite": True},
        headers=admin_auth,
    )
    assert fav2.status_code == 200
    rat2 = await api_client.put(
        f"/api/v1/items/{ep2_id}/rating",
        json={"rating": 9},
        headers=admin_auth,
    )
    assert rat2.status_code == 200

    # 3. Manually re-match the series to "Planet Earth II" (provider_id="1002")
    match_result = await matcher.manual_match(series_id, provider_name="tmdb", provider_id="1002")
    assert match_result.state == "matched"

    # 4. Verify all episodes moved to new metadata and watch states are preserved!
    async with db_sessionmaker() as session:
        series_rematched = await session.get(MediaItem, series_id)
        assert series_rematched is not None
        assert series_rematched.title == "Planet Earth II"
        assert series_rematched.overview == (
            "Wildlife documentary series exploring the world's most iconic habitats."
        )

        # Check constituent episodes
        episodes_rematched = list(
            await session.scalars(
                select(Episode)
                .where(Episode.series_id == series_id)
                .order_by(Episode.episode_number)
            )
        )
        assert len(episodes_rematched) == 3

        # Episode IDs are preserved
        assert episodes_rematched[0].item_id == ep1_id
        assert episodes_rematched[1].item_id == ep2_id
        assert episodes_rematched[2].item_id == ep3_id

        # Episode titles moved to Planet Earth II metadata
        assert episodes_rematched[0].item.title == "Islands"  # was "From Pole to Pole"
        assert episodes_rematched[1].item.title == "Mountains"
        assert episodes_rematched[2].item.title == "Jungles"  # was "Fresh Water"

    # 5. Read back state through API to verify user's watch state survived re-match
    resp_ep1 = await api_client.get(f"/api/v1/episodes/{ep1_id}", headers=admin_auth)
    assert resp_ep1.status_code == 200
    ep1_data = resp_ep1.json()
    assert ep1_data["title"] == "Islands"
    assert ep1_data["watch_state"]["watched"] is True
    assert ep1_data["watch_state"]["position_ms"] == 3000000

    resp_ep2 = await api_client.get(f"/api/v1/episodes/{ep2_id}", headers=admin_auth)
    assert resp_ep2.status_code == 200
    ep2_data = resp_ep2.json()
    assert ep2_data["title"] == "Mountains"
    assert ep2_data["watch_state"]["watched"] is False
    assert ep2_data["watch_state"]["position_ms"] == 1500000
    assert ep2_data["watch_state"]["favorite"] is True
    assert ep2_data["watch_state"]["rating"] == 9
