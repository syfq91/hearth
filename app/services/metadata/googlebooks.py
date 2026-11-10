"""Backward compatibility adapter for Google Books metadata provider.

The core implementation has moved to `plugins/googlebooks/`. This module remains
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
from plugins.googlebooks.plugin import GoogleBooksPlugin

logger = logging.getLogger("hearth.metadata.googlebooks")


class GoogleBooksProvider(MetadataProvider):
    """Backward-compatible adapter for Google Books metadata provider."""

    name = "googlebooks"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        manifest = PluginManifest(
            id="org.hearth.plugins.googlebooks",
            name="Google Books",
            version="1.0.0",
            entrypoint="plugin:GoogleBooksPlugin",
            capabilities=["metadata.book"],
        )
        context = PluginContext(
            settings=settings,
            sessionmaker=None,  # type: ignore[arg-type]
            logger=logger,
            data_dir=Path(settings.data_dir),
            plugin_dir=Path("plugins/googlebooks"),
        )
        self._plugin = GoogleBooksPlugin(context, manifest, client=client)
        self._plugin.apply_configuration({})

    @property
    def enabled(self) -> bool:
        return self._plugin.enabled

    @property
    def base_url(self) -> str:
        return self._plugin.base_url

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
