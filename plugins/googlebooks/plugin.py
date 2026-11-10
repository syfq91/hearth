"""Google Books metadata provider plugin for books."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.plugins.base import MetadataPlugin, PluginContext, PluginStatus
from app.core.plugins.manifest import PluginManifest
from app.services.metadata.base import (
    BookMetadata,
    MovieMetadata,
    ProviderError,
    ProviderImage,
    SearchResult,
)

logger = logging.getLogger("hearth.plugins.googlebooks")


class GoogleBooksConfig(BaseModel):
    """Configuration schema for Google Books plugin."""

    api_key: str = Field(
        default="",
        description="Optional Google Books API key for increased quota",
    )
    enabled: bool = Field(
        default=True,
        description="Whether Google Books metadata lookups are enabled",
    )


class GoogleBooksPlugin(MetadataPlugin[GoogleBooksConfig]):
    """Plugin providing book metadata from Google Books."""

    name = "googlebooks"

    def __init__(
        self,
        context: PluginContext,
        manifest: PluginManifest,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(context, manifest)
        self.base_url = context.settings.google_books_base_url.rstrip("/")
        self._owned_client = client is None
        self._client = client

    @property
    def supported_media_types(self) -> tuple[str, ...]:
        return ("book",)

    @property
    def enabled(self) -> bool:
        if self.status != PluginStatus.ACTIVE:
            return False
        if self.config is not None:
            return self.config.enabled
        return self.context.settings.google_books_enabled

    def apply_configuration(self, config_data: dict[str, Any]) -> None:
        data = dict(config_data)
        if not data.get("api_key") and self.context.settings.google_books_api_key:
            data["api_key"] = self.context.settings.google_books_api_key
        if "enabled" not in data:
            data["enabled"] = self.context.settings.google_books_enabled
        self.config = GoogleBooksConfig(**data)

    async def initialize(self) -> None:
        if self.config is None:
            self.apply_configuration({})
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=10.0,
                headers={"User-Agent": "HearthMediaServer/0.1.0"},
            )

    async def dispose(self) -> None:
        if self._owned_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=10.0,
                headers={"User-Agent": "HearthMediaServer/0.1.0"},
            )
        return self._client

    # ------------------------------------------------------------- base stubs

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        return []

    async def fetch_details(self, provider_id: str) -> MovieMetadata | None:
        return None

    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        return []

    # ------------------------------------------------------------------ books

    async def search_book(
        self,
        query: str,
        author: str | None = None,
        isbn: str | None = None,
    ) -> list[SearchResult]:
        """Search Google Books volumes."""
        if not self.enabled:
            return []

        if isbn:
            q = f"isbn:{isbn.replace('-', '').strip()}"
        else:
            q = f"intitle:{query}"
            if author:
                q += f"+inauthor:{author}"

        params: dict[str, str] = {"q": q, "maxResults": "10"}
        api_key = self.config.api_key if self.config else self.context.settings.google_books_api_key
        if api_key:
            params["key"] = api_key

        client = self._ensure_client()
        try:
            res = await client.get(f"{self.base_url}/volumes", params=params)
            if res.status_code != 200:
                logger.warning("Google Books search status %s", res.status_code)
                return []
            data = res.json()
        except Exception as exc:
            logger.warning("Google Books search failed: %s", exc)
            return []

        items = data.get("items", [])
        results: list[SearchResult] = []
        for item in items:
            v_id = item.get("id")
            info = item.get("volumeInfo", {})
            title = info.get("title")
            if not v_id or not title:
                continue

            year: int | None = None
            pub_date = info.get("publishedDate")
            if pub_date and len(pub_date) >= 4 and pub_date[:4].isdigit():
                year = int(pub_date[:4])

            images = info.get("imageLinks", {})
            poster_url = images.get("thumbnail") or images.get("smallThumbnail")
            overview = info.get("description")

            results.append(
                SearchResult(
                    provider=self.name,
                    provider_id=v_id,
                    title=title,
                    year=year,
                    overview=overview,
                    poster_url=poster_url,
                )
            )
        return results

    async def fetch_book_details(self, book_id: str) -> BookMetadata | None:
        """Fetch volume details by volume id."""
        if not self.enabled:
            return None

        params: dict[str, str] = {}
        api_key = self.config.api_key if self.config else self.context.settings.google_books_api_key
        if api_key:
            params["key"] = api_key

        client = self._ensure_client()
        try:
            res = await client.get(f"{self.base_url}/volumes/{book_id}", params=params)
            if res.status_code == 404:
                return None
            if res.status_code != 200:
                raise ProviderError(f"Google Books failed: HTTP {res.status_code}")
            data = res.json()
        except Exception as exc:
            logger.warning("Google Books lookup failed for %s: %s", book_id, exc)
            return None

        info = data.get("volumeInfo", {})
        title = info.get("title", "")
        authors = info.get("authors", [])
        author_name = authors[0] if authors else None
        publisher = info.get("publisher")
        published_date = info.get("publishedDate")
        overview = info.get("description")
        page_count = info.get("pageCount")
        categories = tuple(info.get("categories", ()))

        images_dict = info.get("imageLinks", {})
        images: list[ProviderImage] = []
        for key in ("extraLarge", "large", "medium", "small", "thumbnail"):
            if key in images_dict:
                images.append(
                    ProviderImage(
                        kind="poster",
                        source_url=images_dict[key],
                        is_primary=True,
                    )
                )
                break

        return BookMetadata(
            provider=self.name,
            provider_id=book_id,
            title=title,
            author_name=author_name,
            publisher=publisher,
            published_date=published_date,
            page_count=page_count,
            overview=overview,
            genres=categories,
            images=tuple(images),
            raw=data,
        )

    async def fetch_book_images(self, book_id: str) -> list[ProviderImage]:
        details = await self.fetch_book_details(book_id)
        return list(details.images) if details else []

    async def close(self) -> None:
        await self.dispose()
