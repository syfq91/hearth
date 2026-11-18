"""Google Books metadata provider plugin for books."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.plugins.base import MetadataPlugin, PluginContext, PluginStatus
from app.core.plugins.manifest import PluginManifest
from app.services.metadata.base import (
    BookMetadata,
    MovieMetadata,
    ProviderImage,
    SearchResult,
)
from app.services.metadata.provider_cache import RateLimiter, cache_get, cache_set

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
        self._limiter = RateLimiter(0.5)

    @property
    def supported_media_types(self) -> tuple[str, ...]:
        return ("book",)

    @property
    def enabled(self) -> bool:
        if self.status != PluginStatus.ACTIVE:
            return False
        return bool(self.config is not None and self.config.enabled)

    def apply_configuration(self, config_data: dict[str, Any]) -> None:
        self.config = GoogleBooksConfig(**config_data)

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

    async def _get_json(
        self, cache_key: str, url: str, params: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        cached = await cache_get(self.context.sessionmaker, self.name, cache_key)
        if cached is not None:
            return cached

        client = self._ensure_client()
        await self._limiter.wait()
        try:
            response = await client.get(url, params=params)
            if response.status_code == 429:
                retry_after = float(response.headers.get("Retry-After", "1") or 1)
                await asyncio.sleep(min(max(retry_after, 0.0), 5.0))
                await self._limiter.wait()
                response = await client.get(url, params=params)
            if response.status_code != 200:
                return None
            payload = response.json()
        except Exception as exc:
            logger.warning("Google Books request failed: %s", exc)
            return None

        if not isinstance(payload, dict):
            return None
        await cache_set(self.context.sessionmaker, self.name, cache_key, payload)
        return payload

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
            # Use the same free-text relevance search as the Google Books site.
            # ``intitle:`` is a strict title-field phrase match that misses
            # volumes whose series/volume number lives in a subtitle or whose
            # title punctuation differs (e.g. "Railgun Vol. 1" vs "...,
            # Vol. 1"). Separate terms with spaces, not a literal '+'.
            q = query
            if author:
                q = f'{q} inauthor:"{author}"'

        params: dict[str, str] = {"q": q, "maxResults": "10"}
        api_key = self.config.api_key if self.config else ""
        if api_key:
            params["key"] = api_key

        # Version the key so results cached under the old intitle query are
        # not replayed after this change.
        cache_key = f"search:v2:{q}"
        data = await self._get_json(cache_key, f"{self.base_url}/volumes", params)
        if data is None:
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
        api_key = self.config.api_key if self.config else ""
        if api_key:
            params["key"] = api_key

        data = await self._get_json(
            f"details:{book_id}", f"{self.base_url}/volumes/{book_id}", params
        )
        if data is None:
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
