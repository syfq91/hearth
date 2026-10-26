"""Tests for MusicBrainz provider: artist/album search, lookups, caching, rate pacing and errors."""

from collections.abc import Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import ProviderCache
from app.services.metadata.base import ProviderError, ProviderUnavailable
from app.services.metadata.musicbrainz import MusicBrainzProvider

MB_ARTIST_SEARCH: dict[str, Any] = {
    "artists": [
        {
            "id": "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d",
            "name": "The Beatles",
            "sort-name": "Beatles, The",
            "score": 100,
            "country": "GB",
            "disambiguation": "legendary 60s British rock band",
        }
    ]
}

MB_ARTIST_DETAILS: dict[str, Any] = {
    "id": "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d",
    "name": "The Beatles",
    "sort-name": "Beatles, The",
    "type": "Group",
    "country": "GB",
    "disambiguation": "legendary 60s British rock band",
    "annotation": {"text": "The Beatles were an English rock band formed in Liverpool."},
    "genres": [{"name": "rock"}, {"name": "pop rock"}],
}

MB_RELEASE_SEARCH: dict[str, Any] = {
    "releases": [
        {
            "id": "a6da631a-e4cc-4299-8ab4-3ab833630f9d",
            "title": "Abbey Road",
            "score": 100,
            "date": "1969-09-26",
            "country": "GB",
        }
    ]
}

MB_RELEASE_DETAILS: dict[str, Any] = {
    "id": "a6da631a-e4cc-4299-8ab4-3ab833630f9d",
    "title": "Abbey Road",
    "date": "1969-09-26",
    "barcode": "077774644624",
    "artist-credit": [
        {
            "name": "The Beatles",
            "artist": {"id": "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d", "name": "The Beatles"},
        }
    ],
    "release-group": {
        "id": "f5093c06-23e3-404f-aeaa-40f720264821",
        "genres": [{"name": "rock"}, {"name": "pop"}],
    },
    "media": [{"disc-count": 1, "track-count": 17, "format": "CD"}],
    "label-info": [{"label": {"name": "Apple Records"}}],
    "genres": [{"name": "rock"}],
}


def _make_provider(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[MusicBrainzProvider, httpx.AsyncClient]:
    settings.musicbrainz_rate_limit_seconds = 0.0
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://musicbrainz.org/ws/2",
    )
    provider = MusicBrainzProvider(settings, sessionmaker, client=client)
    return provider, client


async def test_search_artist(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=MB_ARTIST_SEARCH)

    provider, client = _make_provider(db_settings, db_sessionmaker, handler)
    try:
        results = await provider.search_artist("The Beatles")
        assert len(results) == 1
        assert results[0].provider == "musicbrainz"
        assert results[0].provider_id == "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d"
        assert results[0].title == "The Beatles"
        assert "legendary 60s" in (results[0].overview or "")

        assert len(captured) == 1
        req = captured[0]
        assert "fmt=json" in str(req.url)
        assert req.headers["User-Agent"] == db_settings.musicbrainz_user_agent
    finally:
        await client.aclose()


async def test_fetch_artist_details(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=MB_ARTIST_DETAILS)

    provider, client = _make_provider(db_settings, db_sessionmaker, handler)
    try:
        artist = await provider.fetch_artist_details("b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d")
        assert artist is not None
        assert artist.name == "The Beatles"
        assert artist.sort_name == "Beatles, The"
        assert artist.artist_type == "Group"
        assert artist.country == "GB"
        assert artist.disambiguation == "legendary 60s British rock band"
        assert "English rock band" in (artist.overview or "")
        assert "Rock" in artist.genres
        assert "Pop Rock" in artist.genres
    finally:
        await client.aclose()


async def test_search_album(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=MB_RELEASE_SEARCH)

    provider, client = _make_provider(db_settings, db_sessionmaker, handler)
    try:
        results = await provider.search_album("Abbey Road", artist="The Beatles")
        assert len(results) == 1
        assert results[0].provider_id == "a6da631a-e4cc-4299-8ab4-3ab833630f9d"
        assert results[0].title == "Abbey Road"
        assert results[0].year == 1969
        assert "coverartarchive.org/release/a6da631a-e4cc-4299-8ab4-3ab833630f9d/front" in (
            results[0].poster_url or ""
        )
    finally:
        await client.aclose()


async def test_fetch_album_details(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=MB_RELEASE_DETAILS)

    provider, client = _make_provider(db_settings, db_sessionmaker, handler)
    try:
        album = await provider.fetch_album_details("a6da631a-e4cc-4299-8ab4-3ab833630f9d")
        assert album is not None
        assert album.title == "Abbey Road"
        assert album.artist_name == "The Beatles"
        assert album.artist_provider_id == "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d"
        assert album.release_date == "1969-09-26"
        assert album.year == 1969
        assert album.total_discs == 1
        assert album.total_tracks == 17
        assert album.label == "Apple Records"
        assert album.barcode == "077774644624"
        assert album.release_group_id == "f5093c06-23e3-404f-aeaa-40f720264821"
        assert "Rock" in album.genres
        assert len(album.images) == 1
        assert album.images[0].kind == "poster"
        assert "coverartarchive.org/release/a6da631a-e4cc-4299-8ab4-3ab833630f9d/front" in (
            album.images[0].source_url or ""
        )
    finally:
        await client.aclose()


async def test_fetch_album_images(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    provider, client = _make_provider(db_settings, db_sessionmaker, lambda r: httpx.Response(200))
    try:
        images = await provider.fetch_album_images("a6da631a-e4cc-4299-8ab4-3ab833630f9d")
        assert len(images) == 1
        assert images[0].kind == "poster"
        assert "coverartarchive.org/release/a6da631a-e4cc-4299-8ab4-3ab833630f9d/front" in (
            images[0].source_url or ""
        )
    finally:
        await client.aclose()


async def test_musicbrainz_caching(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=MB_ARTIST_DETAILS)

    provider, client = _make_provider(db_settings, db_sessionmaker, handler)
    try:
        # First call fetches and caches
        artist1 = await provider.fetch_artist_details("b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d")
        assert call_count == 1
        assert artist1 is not None

        # Verify row in ProviderCache
        async with db_sessionmaker() as session:
            count = await session.scalar(
                select(func.count(ProviderCache.key)).where(ProviderCache.provider == "musicbrainz")
            )
            assert count == 1

        # Second call uses cache without making HTTP request
        artist2 = await provider.fetch_artist_details("b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d")
        assert call_count == 1
        assert artist2 is not None
        assert artist2.name == artist1.name
    finally:
        await client.aclose()


async def test_musicbrainz_error_handling(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    # 1. 404 returns None / empty
    def not_found_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    p1, c1 = _make_provider(db_settings, db_sessionmaker, not_found_handler)
    try:
        res = await p1.fetch_artist_details("nonexistent-mbid")
        assert res is None
    finally:
        await c1.aclose()

    # 2. 500 raises ProviderError
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    p2, c2 = _make_provider(db_settings, db_sessionmaker, error_handler)
    try:
        with pytest.raises(ProviderError):
            await p2.fetch_artist_details("error-mbid")
    finally:
        await c2.aclose()

    # 3. Disabled provider raises ProviderUnavailable
    db_settings.musicbrainz_enabled = False
    p3, c3 = _make_provider(db_settings, db_sessionmaker, lambda r: httpx.Response(200))
    try:
        with pytest.raises(ProviderUnavailable):
            await p3.fetch_artist_details("any-mbid")
    finally:
        await c3.aclose()
        db_settings.musicbrainz_enabled = True
