"""Integration tests for the metadata search and manual match endpoints."""

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Library, MediaFile, MediaItem, Movie
from app.services.media.titles import sort_title
from app.services.metadata.base import (
    MovieMetadata,
    ProviderImage,
    ProviderUnavailable,
    SearchResult,
)
from app.services.metadata.matching import MetadataMatcher
from tests.support import FakeProvider

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


def _patch_matcher(monkeypatch, provider: FakeProvider) -> None:
    import app.api.v1.metadata as metadata_module

    def build(settings, app_sessionmaker):  # type: ignore[no-untyped-def]
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
            SearchResult("tmdb", "603", "The Matrix", 1999, popularity=80.0),
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
