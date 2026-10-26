"""Tests for the TMDB provider: mapping, caching, auth and failures."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import ProviderCache
from app.services.metadata.base import ProviderError, ProviderUnavailable
from app.services.metadata.tmdb import TMDBProvider

SEARCH_PAYLOAD: dict[str, Any] = {
    "page": 1,
    "results": [
        {
            "id": 603,
            "title": "The Matrix",
            "original_title": "The Matrix",
            "release_date": "1999-03-31",
            "overview": "A hacker learns the truth.",
            "poster_path": "/matrix-poster.jpg",
            "backdrop_path": "/matrix-backdrop.jpg",
            "popularity": 80.5,
        },
        {
            "id": 604,
            "title": "The Matrix Reloaded",
            "release_date": "2003-05-15",
            "poster_path": None,
            "backdrop_path": None,
            "popularity": 40.0,
        },
    ],
}

DETAILS_PAYLOAD: dict[str, Any] = {
    "id": 603,
    "title": "The Matrix",
    "original_title": "The Matrix",
    "release_date": "1999-03-31",
    "overview": "A hacker learns the truth.",
    "tagline": "Free your mind.",
    "runtime": 136,
    "vote_average": 8.2,
    "imdb_id": "tt0133093",
    "genres": [{"id": 28, "name": "Action"}, {"id": 878, "name": "Science Fiction"}],
    "credits": {
        "cast": [
            {"id": 6384, "name": "Keanu Reeves", "character": "Neo"},
            {"id": 2975, "name": "Laurence Fishburne", "character": "Morpheus"},
        ],
        "crew": [
            {"id": 9330, "name": "Lana Wachowski", "job": "Director"},
            {"id": 9331, "name": "Lilly Wachowski", "job": "Writer"},
        ],
    },
}

IMAGES_PAYLOAD: dict[str, Any] = {
    "posters": [
        {
            "file_path": "/poster-low.jpg",
            "vote_average": 5.0,
            "vote_count": 10,
            "width": 500,
            "height": 750,
            "iso_639_1": "en",
        },
        {
            "file_path": "/poster-high.jpg",
            "vote_average": 8.0,
            "vote_count": 100,
            "width": 1000,
            "height": 1500,
            "iso_639_1": "en",
        },
    ],
    "backdrops": [
        {
            "file_path": "/backdrop.jpg",
            "vote_average": 9.0,
            "vote_count": 100,
            "width": 1920,
            "height": 1080,
            "iso_639_1": None,
        }
    ],
}


def _settings(settings: Settings) -> Settings:
    settings.tmdb_api_key = "test-key"
    settings.tmdb_min_interval_seconds = 0
    settings.tmdb_cache_ttl_hours = 24
    return settings


def _provider(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[TMDBProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.themoviedb.org/3",
    )
    return TMDBProvider(settings, sessionmaker, client=client), client


async def test_search_maps_results_and_sends_api_key(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        results = await provider.search("The Matrix", year=1999)
    finally:
        await client.aclose()

    assert [result.provider_id for result in results] == ["603", "604"]
    assert results[0].title == "The Matrix"
    assert results[0].year == 1999
    assert results[0].poster_url.endswith("/w342/matrix-poster.jpg")
    assert results[1].poster_url is None
    assert requests[0].url.path == "/3/search/movie"
    assert requests[0].url.params["query"] == "The Matrix"
    assert requests[0].url.params["year"] == "1999"
    assert requests[0].url.params["api_key"] == "test-key"
    assert requests[0].url.params["include_adult"] == "false"


async def test_search_is_cached_in_the_database(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        first = await provider.search("The Matrix", year=1999)
        second = await provider.search("The Matrix", year=1999)
    finally:
        await client.aclose()

    assert first == second
    assert calls == 1
    async with db_sessionmaker() as session:
        count = await session.scalar(select(func.count()).select_from(ProviderCache))
        assert count == 1


async def test_fetch_details_maps_credits_and_provider_ids(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=DETAILS_PAYLOAD)

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        details = await provider.fetch_details("603")
    finally:
        await client.aclose()

    assert details is not None
    assert details.title == "The Matrix"
    assert details.year == 1999
    assert details.runtime_seconds == 136 * 60
    assert details.rating == 8.2
    assert details.genres == ("Action", "Science Fiction")
    assert details.provider_ids == {"tmdb": "603", "imdb": "tt0133093"}
    assert {credit.role for credit in details.credits} == {"cast", "director", "writer"}
    director = next(credit for credit in details.credits if credit.role == "director")
    assert director.name == "Lana Wachowski"
    assert requests[0].url.params["append_to_response"] == "credits"


async def test_fetch_images_prefers_votes_and_marks_primary(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=IMAGES_PAYLOAD)

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        images = await provider.fetch_images("603")
    finally:
        await client.aclose()

    posters = [image for image in images if image.kind == "poster"]
    assert [image.source_url for image in posters] == [
        "https://image.tmdb.org/t/p/w780/poster-high.jpg",
        "https://image.tmdb.org/t/p/w780/poster-low.jpg",
    ]
    assert posters[0].is_primary is True
    assert posters[1].is_primary is False
    backdrop = next(image for image in images if image.kind == "backdrop")
    assert backdrop.source_url.endswith("/w1280/backdrop.jpg")


async def test_401_is_unavailable_and_does_not_leak_the_key(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"status_message": "Invalid API key"})

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        with pytest.raises(ProviderUnavailable) as error:
            await provider.search("The Matrix")
    finally:
        await client.aclose()

    assert "test-key" not in str(error.value)


async def test_server_error_is_retryable_and_does_not_leak_the_key(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        with pytest.raises(ProviderError) as error:
            await provider.search("The Matrix")
    finally:
        await client.aclose()

    assert "test-key" not in str(error.value)
    assert "503" in str(error.value)


async def test_429_retries_once(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        results = await provider.search("The Matrix")
    finally:
        await client.aclose()

    assert results
    assert calls == 2


async def test_bearer_token_is_sent_as_a_header(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=SEARCH_PAYLOAD)

    settings = _settings(db_settings)
    settings.tmdb_api_key = "header.payload.signature"
    provider, client = _provider(settings, db_sessionmaker, handler)
    try:
        await provider.search("The Matrix")
    finally:
        await client.aclose()

    assert captured[0].headers["authorization"] == "Bearer header.payload.signature"
    assert "api_key" not in captured[0].url.params


async def test_missing_key_is_unavailable(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    settings = _settings(db_settings)
    settings.tmdb_api_key = None

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made without a key")

    provider, client = _provider(settings, db_sessionmaker, handler)
    try:
        with pytest.raises(ProviderUnavailable, match="not configured"):
            await provider.search("The Matrix")
    finally:
        await client.aclose()


TV_SEARCH_PAYLOAD: dict[str, Any] = {
    "page": 1,
    "results": [
        {
            "id": 1396,
            "name": "Breaking Bad",
            "original_name": "Breaking Bad",
            "first_air_date": "2008-01-20",
            "overview": "A chemistry teacher diagnosed with cancer turns to meth.",
            "poster_path": "/breaking-bad-poster.jpg",
            "backdrop_path": "/breaking-bad-backdrop.jpg",
            "popularity": 120.5,
        },
        {
            "id": 60059,
            "name": "Better Call Saul",
            "first_air_date": "2015-02-08",
            "poster_path": None,
            "backdrop_path": None,
            "popularity": 75.0,
        },
    ],
}

TV_DETAILS_PAYLOAD: dict[str, Any] = {
    "id": 1396,
    "name": "Breaking Bad",
    "original_name": "Breaking Bad",
    "first_air_date": "2008-01-20",
    "status": "Ended",
    "overview": "A chemistry teacher turned meth kingpin.",
    "vote_average": 8.9,
    "networks": [{"id": 174, "name": "AMC"}],
    "genres": [{"id": 18, "name": "Drama"}, {"id": 80, "name": "Crime"}],
    "external_ids": {"imdb_id": "tt0903747", "tvdb_id": 81189},
    "poster_path": "/breaking-bad-poster.jpg",
    "backdrop_path": "/breaking-bad-backdrop.jpg",
    "created_by": [{"id": 66633, "name": "Vince Gilligan"}],
    "credits": {
        "cast": [
            {"id": 17419, "name": "Bryan Cranston", "character": "Walter White"},
            {"id": 84497, "name": "Aaron Paul", "character": "Jesse Pinkman"},
        ],
        "crew": [
            {"id": 66633, "name": "Vince Gilligan", "job": "Executive Producer"},
        ],
    },
    "seasons": [
        {
            "id": 3577,
            "season_number": 0,
            "name": "Specials",
            "overview": "Special episodes.",
            "air_date": "2009-02-17",
            "episode_count": 5,
            "poster_path": "/specials-poster.jpg",
        },
        {
            "id": 3572,
            "season_number": 1,
            "name": "Season 1",
            "overview": "The first season.",
            "air_date": "2008-01-20",
            "episode_count": 7,
            "poster_path": "/s1-poster.jpg",
        },
    ],
}

TV_SEASON_PAYLOAD: dict[str, Any] = {
    "id": 3572,
    "season_number": 1,
    "name": "Season 1",
    "overview": "The first season of Breaking Bad.",
    "air_date": "2008-01-20",
    "poster_path": "/s1-poster.jpg",
    "episodes": [
        {
            "id": 62085,
            "season_number": 1,
            "episode_number": 1,
            "name": "Pilot",
            "overview": "Walter White discovers he has lung cancer.",
            "air_date": "2008-01-20",
            "runtime": 58,
            "vote_average": 8.4,
            "still_path": "/pilot-still.jpg",
            "guest_stars": [{"id": 100, "name": "Marius Stan", "character": "Bogdan Wolynetz"}],
            "crew": [{"id": 66633, "name": "Vince Gilligan", "job": "Director"}],
        },
        {
            "id": 62086,
            "season_number": 1,
            "episode_number": 2,
            "name": "Cat's in the Bag...",
            "overview": "Walt and Jesse attempt to dispose of two bodies.",
            "air_date": "2008-01-27",
            "runtime": 48,
            "vote_average": 8.3,
            "still_path": "/ep2-still.jpg",
            "guest_stars": [],
            "crew": [],
        },
    ],
}

TV_EPISODE_PAYLOAD: dict[str, Any] = {
    "id": 62085,
    "season_number": 1,
    "episode_number": 1,
    "name": "Pilot",
    "overview": "Walter White discovers he has lung cancer.",
    "air_date": "2008-01-20",
    "runtime": 58,
    "vote_average": 8.4,
    "still_path": "/pilot-still.jpg",
    "external_ids": {"imdb_id": "tt0959621"},
    "guest_stars": [{"id": 100, "name": "Marius Stan", "character": "Bogdan Wolynetz"}],
    "crew": [{"id": 66633, "name": "Vince Gilligan", "job": "Director"}],
}

TV_IMAGES_PAYLOAD: dict[str, Any] = {
    "posters": [
        {
            "file_path": "/tv-poster.jpg",
            "vote_average": 8.5,
            "vote_count": 50,
            "width": 1000,
            "height": 1500,
        }
    ],
    "backdrops": [
        {
            "file_path": "/tv-backdrop.jpg",
            "vote_average": 9.0,
            "vote_count": 80,
            "width": 1920,
            "height": 1080,
        }
    ],
}


async def test_search_tv_maps_results_and_sends_first_air_date_year(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=TV_SEARCH_PAYLOAD)

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        results = await provider.search_tv("Breaking Bad", year=2008)
    finally:
        await client.aclose()

    assert [result.provider_id for result in results] == ["1396", "60059"]
    assert results[0].title == "Breaking Bad"
    assert results[0].year == 2008
    assert results[0].poster_url.endswith("/w342/breaking-bad-poster.jpg")
    assert results[1].poster_url is None
    assert requests[0].url.path == "/3/search/tv"
    assert requests[0].url.params["query"] == "Breaking Bad"
    assert requests[0].url.params["first_air_date_year"] == "2008"
    assert requests[0].url.params["api_key"] == "test-key"


async def test_fetch_tv_details_maps_series_seasons_and_credits(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=TV_DETAILS_PAYLOAD)

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        details = await provider.fetch_tv_details("1396")
    finally:
        await client.aclose()

    assert details is not None
    assert details.title == "Breaking Bad"
    assert details.original_title == "Breaking Bad"
    assert details.year == 2008
    assert details.first_air_date == "2008-01-20"
    assert details.status == "Ended"
    assert details.network == "AMC"
    assert details.rating == 8.9
    assert details.genres == ("Drama", "Crime")
    assert details.provider_ids == {"tmdb": "1396", "imdb": "tt0903747", "tvdb": "81189"}
    assert len(details.seasons) == 2
    specials, s1 = details.seasons
    assert specials.season_number == 0
    assert specials.title == "Specials"
    assert specials.episode_count == 5
    assert s1.season_number == 1
    assert s1.episode_count == 7
    # Credits
    assert any(c.name == "Bryan Cranston" and c.role == "cast" for c in details.credits)
    assert any(c.name == "Vince Gilligan" and c.role == "creator" for c in details.credits)
    assert requests[0].url.params["append_to_response"] == "credits,external_ids"


async def test_fetch_tv_season_maps_episodes_stills_and_credits(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=TV_SEASON_PAYLOAD)

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        season = await provider.fetch_tv_season("1396", 1)
    finally:
        await client.aclose()

    assert season is not None
    assert season.season_number == 1
    assert season.title == "Season 1"
    assert season.poster_url.endswith("/w780/s1-poster.jpg")
    assert len(season.episodes) == 2
    ep1, ep2 = season.episodes
    assert ep1.episode_number == 1
    assert ep1.title == "Pilot"
    assert ep1.runtime_seconds == 58 * 60
    assert ep1.rating == 8.4
    assert ep1.still_url.endswith("/w780/pilot-still.jpg")
    assert ep1.provider_ids == {"tmdb": "62085"}
    assert any(c.name == "Vince Gilligan" and c.role == "director" for c in ep1.credits)
    assert any(c.name == "Marius Stan" and c.role == "cast" for c in ep1.credits)
    assert ep2.episode_number == 2
    assert ep2.title == "Cat's in the Bag..."
    assert ep2.runtime_seconds == 48 * 60


async def test_fetch_tv_episode_maps_single_episode(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=TV_EPISODE_PAYLOAD)

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        episode = await provider.fetch_tv_episode("1396", 1, 1)
    finally:
        await client.aclose()

    assert episode is not None
    assert episode.episode_number == 1
    assert episode.season_number == 1
    assert episode.title == "Pilot"
    assert episode.provider_ids == {"tmdb": "62085", "imdb": "tt0959621"}
    assert requests[0].url.path == "/3/tv/1396/season/1/episode/1"


async def test_fetch_tv_images_fetches_posters_and_backdrops(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=TV_IMAGES_PAYLOAD)

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        images = await provider.fetch_tv_images("1396")
    finally:
        await client.aclose()

    assert len(images) == 2
    assert any(img.kind == "poster" and "/w780/tv-poster.jpg" in img.source_url for img in images)
    assert any(
        img.kind == "backdrop" and "/w1280/tv-backdrop.jpg" in img.source_url for img in images
    )
    assert requests[0].url.path == "/3/tv/1396/images"


async def test_search_tv_and_details_cached(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=TV_SEARCH_PAYLOAD)

    provider, client = _provider(_settings(db_settings), db_sessionmaker, handler)
    try:
        first = await provider.search_tv("Breaking Bad", year=2008)
        second = await provider.search_tv("Breaking Bad", year=2008)
    finally:
        await client.aclose()

    assert first == second
    assert calls == 1
    async with db_sessionmaker() as session:
        count = await session.scalar(select(func.count()).select_from(ProviderCache))
        assert count == 1
