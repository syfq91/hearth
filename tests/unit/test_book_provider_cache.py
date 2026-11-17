"""Regression tests for book-provider response caching."""

from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.db.migrate import run_migrations_async
from app.db.session import build_engine, build_sessionmaker
from app.services.metadata.googlebooks import GoogleBooksProvider
from app.services.metadata.openlibrary import OpenLibraryProvider


@pytest.fixture
async def sessionmaker(tmp_path: Path) -> async_sessionmaker:
    settings = Settings(
        env="testing",
        log_level="WARNING",
        data_dir=tmp_path,
        db_path=tmp_path / "hearth.db",
        media_dir=tmp_path / "media",
        render_device=tmp_path / "renderD128",
        secret_key="test-secret-key",
    )
    await run_migrations_async(settings)
    engine = build_engine(settings)
    try:
        yield build_sessionmaker(engine)
    finally:
        await engine.dispose()


async def test_openlibrary_reuses_cached_search(sessionmaker: async_sessionmaker) -> None:
    calls = 0
    payload = {"docs": [{"key": "/works/OL1W", "title": "Dune"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=payload)

    settings = Settings(env="testing", data_dir=Path("."), openlibrary_enabled=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenLibraryProvider(settings, sessionmaker=sessionmaker, client=client)

    first = await provider.search_book("Dune")
    second = await provider.search_book("Dune")

    assert [item.provider_id for item in first] == ["/works/OL1W"]
    assert [item.provider_id for item in second] == ["/works/OL1W"]
    assert calls == 1
    await provider.close()


async def test_googlebooks_reuses_cached_details(sessionmaker: async_sessionmaker) -> None:
    calls = 0
    payload = {"volumeInfo": {"title": "Dune", "authors": ["Frank Herbert"]}}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=payload)

    settings = Settings(env="testing", data_dir=Path("."), google_books_enabled=True)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GoogleBooksProvider(settings, sessionmaker=sessionmaker, client=client)

    first = await provider.fetch_book_details("vol-1")
    second = await provider.fetch_book_details("vol-1")

    assert first is not None and first.title == "Dune"
    assert second is not None and second.title == "Dune"
    assert calls == 1
    await provider.close()
