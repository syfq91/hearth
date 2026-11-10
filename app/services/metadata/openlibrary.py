"""Backward compatibility adapter for Open Library metadata provider.

The core implementation has moved to `plugins/openlibrary/`. This module remains
to preserve existing imports and constructor signatures.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.core.config import Settings
from app.core.plugins.base import PluginContext
from app.core.plugins.manifest import PluginManifest
from app.services.metadata.base import (
    BookMetadata,
    MetadataProvider,
    MovieMetadata,
    ProviderImage,
    SearchResult,
)
from plugins.openlibrary.plugin import OpenLibraryPlugin

logger = logging.getLogger("hearth.metadata.openlibrary")


class OpenLibraryProvider(MetadataProvider):
    """Backward-compatible adapter for Open Library metadata provider."""

    name = "openlibrary"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        manifest = PluginManifest(
            id="org.hearth.plugins.openlibrary",
            name="Open Library",
            version="1.0.0",
            entrypoint="plugin:OpenLibraryPlugin",
            capabilities=["metadata.book"],
        )
        context = PluginContext(
            settings=settings,
            sessionmaker=None,  # type: ignore[arg-type]
            logger=logger,
            data_dir=Path(settings.data_dir),
            plugin_dir=Path("plugins/openlibrary"),
        )
        self._plugin = OpenLibraryPlugin(context, manifest, client=client)
        self._plugin.apply_configuration({})

    @property
    def enabled(self) -> bool:
        return self._plugin.enabled

    @property
    def base_url(self) -> str:
        return self._plugin.base_url

    @property
    def covers_base_url(self) -> str:
        return self._plugin.covers_base_url

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        return await self._plugin.search(query, year)

    async def fetch_details(self, provider_id: str) -> MovieMetadata | None:
        return await self._plugin.fetch_details(provider_id)

    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        return await self._plugin.fetch_images(provider_id)

    async def search_book(
        self,
        query: str,
        author: str | None = None,
        isbn: str | None = None,
    ) -> list[SearchResult]:
        return await self._plugin.search_book(query, author=author, isbn=isbn)

    async def fetch_book_details(self, book_id: str) -> BookMetadata | None:
        return await self._plugin.fetch_book_details(book_id)

    async def fetch_book_images(self, book_id: str) -> list[ProviderImage]:
        return await self._plugin.fetch_book_images(book_id)

    async def close(self) -> None:
        await self._plugin.close()
