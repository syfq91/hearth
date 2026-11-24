"""Reusable fakes for metadata tests."""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.plugins.base import MetadataPlugin, PluginContext
from app.core.plugins.manifest import PluginManifest
from app.services.metadata.base import (
    BookMetadata,
    MetadataProvider,
    MovieMetadata,
    ProviderImage,
    SearchResult,
)


def make_metadata_plugin(
    plugin_cls: type[MetadataPlugin],
    *,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
    client: httpx.AsyncClient | None = None,
    config: dict[str, Any] | None = None,
) -> Any:
    """Build a metadata plugin with an injected HTTP client for tests.

    API keys and enabled flags are supplied through ``config``; they are no
    longer read from environment-backed ``Settings`` fields.
    """
    name = plugin_cls.name
    manifest = PluginManifest(
        id=f"org.hearth.plugins.{name}",
        name=name,
        version="1.0.0",
        entrypoint=f"plugin:{plugin_cls.__name__}",
        capabilities=[],
    )
    context = PluginContext(
        settings=settings,
        sessionmaker=sessionmaker,  # type: ignore[arg-type]
        logger=logging.getLogger(f"hearth.test.{name}"),
        data_dir=Path(settings.data_dir),
        plugin_dir=Path("plugins") / name,
    )
    plugin = plugin_cls(context, manifest, client=client)
    plugin.apply_configuration(config or {})
    return plugin


class FakeProvider(MetadataProvider):
    """A scripted remote provider with call counters."""

    name = "tmdb"

    def __init__(
        self,
        *,
        results: Sequence[SearchResult] | None = None,
        details: dict[str, MovieMetadata] | None = None,
        images: Sequence[ProviderImage] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.results = list(results or [])
        self.details = dict(details or {})
        self.image_list = list(images or [])
        self.error = error
        self.search_calls = 0
        self.detail_calls = 0
        self.image_calls = 0

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        self.search_calls += 1
        if self.error is not None:
            raise self.error
        return list(self.results)

    async def fetch_details(self, provider_id: str) -> MovieMetadata | None:
        self.detail_calls += 1
        if self.error is not None:
            raise self.error
        return self.details.get(provider_id)

    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        self.image_calls += 1
        if self.error is not None:
            raise self.error
        return list(self.image_list)


class FakeBookProvider(FakeProvider):
    """A scripted book provider exposing the book-specific provider surface."""

    name = "googlebooks"

    def __init__(
        self,
        *,
        book_results: Sequence[SearchResult] | None = None,
        book_details: dict[str, BookMetadata] | None = None,
        book_images: Sequence[ProviderImage] | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__(error=error)
        self.book_results = list(book_results or [])
        self.book_details = dict(book_details or {})
        self.book_images = list(book_images or [])
        self.book_search_calls: list[tuple[str, str | None, str | None]] = []
        self.book_detail_calls: list[str] = []

    async def search_book(
        self, query: str, author: str | None = None, isbn: str | None = None
    ) -> list[SearchResult]:
        self.book_search_calls.append((query, author, isbn))
        if self.error is not None:
            raise self.error
        return list(self.book_results)

    async def fetch_book_details(self, book_id: str) -> BookMetadata | None:
        self.book_detail_calls.append(book_id)
        if self.error is not None:
            raise self.error
        return self.book_details.get(book_id)

    async def fetch_book_images(self, book_id: str) -> list[ProviderImage]:
        if self.error is not None:
            raise self.error
        return list(self.book_images)


class FakeArtwork:
    """Record artwork handed to the image pipeline."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[ProviderImage]]] = []

    async def __call__(self, item_id: str, provider: str, images: Sequence[ProviderImage]) -> int:
        self.calls.append((item_id, provider, list(images)))
        return len(images)
