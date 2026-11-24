"""Integration tests for the metadata search and manual match endpoints."""

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    Author,
    Book,
    Episode,
    Library,
    MediaFile,
    MediaItem,
    Movie,
    Season,
    Series,
)
from app.services.media.titles import sort_title
from app.services.metadata.base import (
    BookMetadata,
    EpisodeMetadata,
    MovieMetadata,
    ProviderImage,
    ProviderRateLimited,
    ProviderUnavailable,
    SearchResult,
    SeasonMetadata,
)
from app.services.metadata.local import LocalProvider
from app.services.metadata.matching import MetadataMatcher
from tests.support import FakeBookProvider, FakeProvider

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _seed_item(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
    async with sessionmaker() as session:
        library = Library(name=f"Movies {uuid.uuid4().hex[:8]}", type="movie")
        session.add(library)
        await session.flush()
        item = MediaItem(
            library_id=library.id,
            type="movie",
            title="The Matrix",
            sort_title=sort_title("The Matrix"),
            year=1999,
            match_state="pending",
            next_match_at=datetime.now(UTC),
        )
        item.movie = Movie()
        item.files.append(
            MediaFile(
                path=f"/media/{uuid.uuid4().hex}.mkv",
                size_bytes=1024,
                mtime=1.0,
                partial_hash="a" * 64,
                duration_seconds=8160.0,
            )
        )
        session.add(item)
        await session.commit()
        return item.id


async def _seed_book(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
    async with sessionmaker() as session:
        library = Library(name=f"Books {uuid.uuid4().hex[:8]}", type="book")
        session.add(library)
        await session.flush()
        author = Author(name="Kazuma Kamachi")
        session.add(author)
        await session.flush()
        item = MediaItem(
            library_id=library.id,
            type="book",
            title="A Certain Scientific Railgun Vol. 1",
            sort_title=sort_title("A Certain Scientific Railgun Vol. 1"),
            match_state="unmatched",
        )
        item.book = Book(author=author)
        session.add(item)
        await session.commit()
        return item.id


def _patch_matcher(monkeypatch, provider: FakeProvider) -> None:
    import app.api.v1.metadata as metadata_module

    def build(settings, app_sessionmaker, plugin_manager):  # type: ignore[no-untyped-def]
        return MetadataMatcher(settings, app_sessionmaker, providers=[provider])

    monkeypatch.setattr(metadata_module, "build_matcher", build)


async def test_manual_match_endpoint_attaches_candidate(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    admin = await _setup(api_client)
    item_id = await _seed_item(db_sessionmaker)
    provider = FakeProvider(
        details={
            "603": MovieMetadata(
                provider="tmdb",
                provider_id="603",
                title="The Matrix",
                year=1999,
                runtime_seconds=8160,
                overview="A hacker learns the truth.",
                genres=("Action",),
            )
        },
        images=[ProviderImage(kind="poster", source_url="https://img/p.jpg", is_primary=True)],
    )
    _patch_matcher(monkeypatch, provider)

    response = await api_client.post(
        f"/api/v1/items/{item_id}/match",
        json={"provider": "tmdb", "provider_id": "603"},
        headers=_bearer(admin),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "matched"
    assert body["provider"] == "tmdb"
    assert body["provider_id"] == "603"

    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None
        assert item.match_state == "matched"
        assert item.title == "The Matrix"


async def test_manual_match_unknown_item_is_404(api_client: httpx.AsyncClient, monkeypatch) -> None:
    admin = await _setup(api_client)
    _patch_matcher(monkeypatch, FakeProvider())

    response = await api_client.post(
        "/api/v1/items/does-not-exist/match",
        json={"provider": "tmdb", "provider_id": "603"},
        headers=_bearer(admin),
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "item_not_found"


async def test_manual_match_provider_error_is_502(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    admin = await _setup(api_client)
    item_id = await _seed_item(db_sessionmaker)
    provider = FakeProvider(error=ProviderUnavailable("TMDB rejected the configured API key"))
    _patch_matcher(monkeypatch, provider)

    response = await api_client.post(
        f"/api/v1/items/{item_id}/match",
        json={"provider": "tmdb", "provider_id": "603"},
        headers=_bearer(admin),
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "metadata_provider_error"


async def test_search_endpoint_returns_and_filters_results(
    api_client: httpx.AsyncClient, monkeypatch
) -> None:
    admin = await _setup(api_client)
    provider = FakeProvider(
        results=[
            SearchResult(
                "tmdb",
                "603",
                "The Matrix",
                1999,
                popularity=80.0,
                source_url="https://www.themoviedb.org/movie/603",
            ),
            SearchResult("tmdb", "604", "The Matrix Reloaded", 2003, popularity=40.0),
        ]
    )
    _patch_matcher(monkeypatch, provider)

    response = await api_client.get(
        "/api/v1/metadata/search",
        params={"query": "The Matrix", "year": 1999},
        headers=_bearer(admin),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert [result["provider_id"] for result in body] == ["603", "604"]
    assert body[0]["source_url"] == "https://www.themoviedb.org/movie/603"
    assert body[1]["source_url"] is None

    filtered = await api_client.get(
        "/api/v1/metadata/search",
        params={"query": "The Matrix", "provider": "nope"},
        headers=_bearer(admin),
    )
    assert filtered.status_code == 422
    assert filtered.json()["error"]["code"] == "unknown_provider"


async def test_search_requires_admin(api_client: httpx.AsyncClient, monkeypatch) -> None:
    _patch_matcher(monkeypatch, FakeProvider(results=[]))

    response = await api_client.get("/api/v1/metadata/search", params={"query": "The Matrix"})

    assert response.status_code == 401


async def test_providers_endpoint_lists_enabled_online_providers(
    api_client: httpx.AsyncClient, monkeypatch
) -> None:
    import app.api.v1.metadata as metadata_module

    class DisabledProvider(FakeProvider):
        name = "disabled"
        enabled = False  # type: ignore[assignment]

    admin = await _setup(api_client)

    def build(settings, app_sessionmaker, plugin_manager):  # type: ignore[no-untyped-def]
        return MetadataMatcher(
            settings,
            app_sessionmaker,
            providers=[LocalProvider(), FakeProvider(), DisabledProvider()],
        )

    monkeypatch.setattr(metadata_module, "build_matcher", build)

    response = await api_client.get("/api/v1/metadata/providers", headers=_bearer(admin))
    assert response.status_code == 200, response.text
    assert response.json() == ["tmdb"]

    anonymous = await api_client.get("/api/v1/metadata/providers")
    assert anonymous.status_code == 401


async def test_book_search_search_endpoint_uses_book_provider(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    admin = await _setup(api_client)
    item_id = await _seed_book(db_sessionmaker)
    provider = FakeBookProvider(
        book_results=[
            SearchResult(
                "googlebooks",
                "VOL1",
                "A Certain Scientific Railgun, Vol. 1",
                2011,
            )
        ]
    )
    _patch_matcher(monkeypatch, provider)

    response = await api_client.get(
        "/api/v1/metadata/search",
        params={"query": "A Certain Scientific Railgun Vol. 1", "item_id": item_id},
        headers=_bearer(admin),
    )

    assert response.status_code == 200, response.text
    assert [result["provider_id"] for result in response.json()] == ["VOL1"]
    assert provider.book_search_calls == [
        ("A Certain Scientific Railgun Vol. 1", "Kazuma Kamachi", None)
    ]


async def test_manual_match_book_uses_book_provider(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    admin = await _setup(api_client)
    item_id = await _seed_book(db_sessionmaker)
    provider = FakeBookProvider(
        book_details={
            "VOL1": BookMetadata(
                provider="googlebooks",
                provider_id="VOL1",
                title="A Certain Scientific Railgun, Vol. 1",
                author_name="Kazuma Kamachi",
                published_date="2011",
            )
        }
    )
    _patch_matcher(monkeypatch, provider)

    response = await api_client.post(
        f"/api/v1/items/{item_id}/match",
        json={"provider": "googlebooks", "provider_id": "VOL1"},
        headers=_bearer(admin),
    )

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "matched"
    assert provider.book_detail_calls == ["VOL1"]
    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None
        assert item.match_state == "matched"
        assert item.year == 2011


async def test_search_reuses_one_matcher_for_the_app(
    api_client: httpx.AsyncClient, monkeypatch
) -> None:
    import app.api.v1.metadata as metadata_module

    admin = await _setup(api_client)
    provider = FakeProvider(results=[SearchResult("tmdb", "603", "The Matrix", 1999)])
    builds: list[int] = []

    def build(settings, app_sessionmaker, plugin_manager):  # type: ignore[no-untyped-def]
        builds.append(1)
        return MetadataMatcher(settings, app_sessionmaker, providers=[provider])

    monkeypatch.setattr(metadata_module, "build_matcher", build)

    for _ in range(2):
        response = await api_client.get(
            "/api/v1/metadata/search",
            params={"query": "The Matrix"},
            headers=_bearer(admin),
        )
        assert response.status_code == 200, response.text

    assert len(builds) == 1


async def _seed_episode(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
    async with sessionmaker() as session:
        library = Library(name=f"TV {uuid.uuid4().hex[:8]}", type="tv")
        session.add(library)
        await session.flush()
        series = MediaItem(
            library_id=library.id,
            type="series",
            title="Breaking Bad",
            sort_title=sort_title("Breaking Bad"),
            match_state="unmatched",
        )
        series.series = Series()
        session.add(series)
        await session.flush()
        episode = MediaItem(
            library_id=library.id,
            type="episode",
            title="Breaking Bad S01E04",
            sort_title=sort_title("Breaking Bad S01E04"),
            match_state="unmatched",
        )
        episode.episode = Episode(series_id=series.id, season_number=1, episode_number=4)
        session.add(episode)
        await session.commit()
        return episode.id


class FakeTVProvider(FakeProvider):
    """Scripted provider exposing the TV episode surface."""

    def __init__(
        self,
        episodes: dict[tuple[str, int, int], EpisodeMetadata] | None = None,
        seasons: dict[tuple[str, int], SeasonMetadata] | None = None,
    ) -> None:
        super().__init__()
        self.episodes = episodes or {}
        self.seasons = seasons or {}
        self.episode_calls: list[tuple[str, int, int]] = []
        self.season_calls: list[tuple[str, int]] = []

    async def fetch_tv_episode(
        self, series_id: str, season_number: int, episode_number: int
    ) -> EpisodeMetadata | None:
        self.episode_calls.append((series_id, season_number, episode_number))
        return self.episodes.get((series_id, season_number, episode_number))

    async def fetch_tv_season(self, series_id: str, season_number: int) -> SeasonMetadata | None:
        self.season_calls.append((series_id, season_number))
        return self.seasons.get((series_id, season_number))


async def test_resolve_url_fetches_book_metadata_directly(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    admin = await _setup(api_client)
    item_id = await _seed_book(db_sessionmaker)
    provider = FakeBookProvider(
        book_details={
            "B1R1CwAAQBAJ": BookMetadata(
                provider="googlebooks",
                provider_id="B1R1CwAAQBAJ",
                title="Dune",
                published_date="1965",
                overview="Set on the desert planet Arrakis.",
            )
        }
    )
    _patch_matcher(monkeypatch, provider)

    url = "https://books.google.com/books?id=B1R1CwAAQBAJ"
    response = await api_client.post(
        "/api/v1/metadata/resolve",
        json={"item_id": item_id, "url": url},
        headers=_bearer(admin),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["provider"] == "googlebooks"
    assert body[0]["provider_id"] == "B1R1CwAAQBAJ"
    assert body[0]["title"] == "Dune"
    assert body[0]["year"] == 1965
    assert body[0]["source_url"] == url
    assert body[0]["season_number"] is None
    assert body[0]["episode_number"] is None
    assert provider.book_detail_calls == ["B1R1CwAAQBAJ"]


async def test_resolve_url_maps_provider_rate_limit_to_distinct_error(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    admin = await _setup(api_client)
    item_id = await _seed_book(db_sessionmaker)
    provider = FakeBookProvider(error=ProviderRateLimited("Google Books quota exceeded (HTTP 429)"))
    _patch_matcher(monkeypatch, provider)

    response = await api_client.post(
        "/api/v1/metadata/resolve",
        json={
            "item_id": item_id,
            "url": "https://books.google.com/books?id=EELNEQAAQBAJ",
        },
        headers=_bearer(admin),
    )

    assert response.status_code == 502, response.text
    body = response.json()["error"]
    assert body["code"] == "metadata_provider_rate_limited"
    assert "quota exceeded" in body["message"]


async def test_resolve_url_fetches_episode_metadata_directly(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    admin = await _setup(api_client)
    item_id = await _seed_episode(db_sessionmaker)
    provider = FakeTVProvider(
        episodes={
            ("1396", 1, 4): EpisodeMetadata(
                provider="tmdb",
                provider_id="62085",
                series_provider_id="1396",
                season_number=1,
                episode_number=4,
                title="Pilot",
                overview="Walter White gets a diagnosis.",
                air_date="2008-01-20",
            )
        }
    )
    _patch_matcher(monkeypatch, provider)

    url = "https://www.themoviedb.org/tv/1396/season/1/episode/4"
    response = await api_client.post(
        "/api/v1/metadata/resolve",
        json={"item_id": item_id, "url": url},
        headers=_bearer(admin),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["provider"] == "tmdb"
    assert body[0]["provider_id"] == "1396"
    assert body[0]["title"] == "Pilot"
    assert body[0]["year"] == 2008
    assert body[0]["source_url"] == url
    assert body[0]["season_number"] == 1
    assert body[0]["episode_number"] == 4
    assert provider.episode_calls == [("1396", 1, 4)]


async def test_resolve_url_rejects_unsupported_and_mismatched_urls(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    admin = await _setup(api_client)
    item_id = await _seed_item(db_sessionmaker)
    _patch_matcher(monkeypatch, FakeProvider())

    unsupported = await api_client.post(
        "/api/v1/metadata/resolve",
        json={"item_id": item_id, "url": "https://example.com/movie/603"},
        headers=_bearer(admin),
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "invalid_provider_url"

    mismatched = await api_client.post(
        "/api/v1/metadata/resolve",
        json={"item_id": item_id, "url": "https://books.google.com/books?id=B1R1CwAAQBAJ"},
        headers=_bearer(admin),
    )
    assert mismatched.status_code == 422
    assert mismatched.json()["error"]["code"] == "invalid_provider_url"


async def test_manual_match_episode_applies_resolved_metadata(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    admin = await _setup(api_client)
    item_id = await _seed_episode(db_sessionmaker)
    provider = FakeTVProvider(
        episodes={
            ("1396", 1, 4): EpisodeMetadata(
                provider="tmdb",
                provider_id="62085",
                series_provider_id="1396",
                season_number=1,
                episode_number=4,
                title="Pilot",
                overview="Walter White gets a diagnosis.",
                air_date="2008-01-20",
                runtime_seconds=3600,
                rating=9.0,
                provider_ids={"tmdb": "62085", "imdb": "tt0959621"},
            )
        }
    )
    _patch_matcher(monkeypatch, provider)

    response = await api_client.post(
        f"/api/v1/items/{item_id}/match",
        json={
            "provider": "tmdb",
            "provider_id": "1396",
            "season_number": 1,
            "episode_number": 4,
        },
        headers=_bearer(admin),
    )

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "matched"
    assert provider.episode_calls == [("1396", 1, 4)]

    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None
        assert item.title == "Pilot"
        assert item.overview == "Walter White gets a diagnosis."
        assert item.match_state == "matched"
        assert item.runtime_seconds == 3600
        assert item.episode is not None
        assert item.episode.season_number == 1
        assert item.episode.episode_number == 4
        assert item.episode.air_date == "2008-01-20"
        assert item.provider_ids["tmdb"] == "62085"
        assert item.provider_ids["tmdb_series"] == "1396"


async def test_resolve_and_apply_season_url(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    monkeypatch,
) -> None:
    admin = await _setup(api_client)
    async with db_sessionmaker() as session:
        library = Library(name=f"TV {uuid.uuid4().hex[:8]}", type="tv")
        session.add(library)
        await session.flush()
        series = MediaItem(
            library_id=library.id,
            type="series",
            title="Breaking Bad",
            sort_title=sort_title("Breaking Bad"),
            match_state="unmatched",
        )
        series.series = Series()
        session.add(series)
        await session.flush()
        season = MediaItem(
            library_id=library.id,
            type="season",
            title="Breaking Bad Season 2",
            sort_title=sort_title("Breaking Bad Season 2"),
            match_state="unmatched",
        )
        season.season = Season(series_id=series.id, season_number=2)
        session.add(season)
        await session.commit()
        item_id = season.id

    provider = FakeTVProvider(
        seasons={
            ("1396", 2): SeasonMetadata(
                provider="tmdb",
                provider_id="3581",
                series_provider_id="1396",
                season_number=2,
                title="Season 2",
                overview="The second season.",
                air_date="2009-03-15",
            )
        }
    )
    _patch_matcher(monkeypatch, provider)

    resolve = await api_client.post(
        "/api/v1/metadata/resolve",
        json={
            "item_id": item_id,
            "url": "https://www.themoviedb.org/tv/1396-breaking-bad/season/2",
        },
        headers=_bearer(admin),
    )
    assert resolve.status_code == 200, resolve.text
    candidate = resolve.json()[0]
    assert candidate["title"] == "Season 2"
    assert candidate["season_number"] == 2
    assert candidate["episode_number"] is None

    applied = await api_client.post(
        f"/api/v1/items/{item_id}/match",
        json={
            "provider": candidate["provider"],
            "provider_id": candidate["provider_id"],
            "season_number": candidate["season_number"],
        },
        headers=_bearer(admin),
    )
    assert applied.status_code == 200, applied.text
    assert provider.season_calls == [("1396", 2), ("1396", 2)]

    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None
        assert item.title == "Season 2"
        assert item.match_state == "matched"
        assert item.provider_ids["tmdb"] == "3581"
        assert item.provider_ids["tmdb_series"] == "1396"
