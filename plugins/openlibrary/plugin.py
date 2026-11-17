"""Open Library metadata provider plugin for books and covers."""

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

logger = logging.getLogger("hearth.plugins.openlibrary")


class OpenLibraryConfig(BaseModel):
    """Configuration schema for Open Library plugin."""

    enabled: bool = Field(
        default=True,
        description="Whether Open Library metadata lookups are enabled",
    )


class OpenLibraryPlugin(MetadataPlugin[OpenLibraryConfig]):
    """Plugin providing book metadata from Open Library."""

    name = "openlibrary"

    def __init__(
        self,
        context: PluginContext,
        manifest: PluginManifest,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(context, manifest)
        self.base_url = context.settings.openlibrary_base_url.rstrip("/")
        self.covers_base_url = context.settings.openlibrary_covers_base_url.rstrip("/")
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
        if self.config is not None:
            return self.config.enabled
        return self.context.settings.openlibrary_enabled

    def apply_configuration(self, config_data: dict[str, Any]) -> None:
        data = dict(config_data)
        if "enabled" not in data:
            data["enabled"] = self.context.settings.openlibrary_enabled
        self.config = OpenLibraryConfig(**data)

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
            logger.warning("Open Library request failed: %s", exc)
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
        """Search Open Library for book candidates by title/author or ISBN."""
        if not self.enabled:
            return []

        params: dict[str, str] = {"limit": "10"}
        if isbn:
            clean_isbn = isbn.replace("-", "").strip()
            params["isbn"] = clean_isbn
        else:
            params["title"] = query
            if author:
                params["author"] = author

        cache_key = "search:" + (f"isbn:{params['isbn']}" if isbn else f"{query}:{author or ''}")
        data = await self._get_json(cache_key, f"{self.base_url}/search.json", params)
        if data is None:
            return []

        docs = data.get("docs", [])
        results: list[SearchResult] = []
        for doc in docs:
            key = doc.get("key")
            title = doc.get("title")
            if not key or not title:
                continue
            year = doc.get("first_publish_year")
            cover_i = doc.get("cover_i")
            poster_url = f"{self.covers_base_url}/b/id/{cover_i}-L.jpg" if cover_i else None
            overview = doc.get("first_sentence", [None])
            if isinstance(overview, list):
                overview = overview[0] if overview else None

            results.append(
                SearchResult(
                    provider=self.name,
                    provider_id=key,
                    title=title,
                    year=year,
                    overview=overview,
                    poster_url=poster_url,
                )
            )
        return results

    async def fetch_book_details(self, book_id: str) -> BookMetadata | None:
        """Fetch normalized book details from Open Library."""
        if not self.enabled:
            return None

        clean_id = book_id.strip()
        if not clean_id.startswith("/"):
            if clean_id.startswith("OL") and clean_id.endswith("W"):
                path = f"/works/{clean_id}.json"
            elif clean_id.startswith("OL") and clean_id.endswith("M"):
                path = f"/books/{clean_id}.json"
            else:
                path = f"/{clean_id}.json"
        else:
            path = f"{clean_id}.json" if not clean_id.endswith(".json") else clean_id

        data = await self._get_json(f"details:{path}", f"{self.base_url}{path}")
        if data is None:
            return None

        title = data.get("title", "")
        desc_raw = data.get("description")
        overview = desc_raw.get("value") if isinstance(desc_raw, dict) else desc_raw
        subjects = tuple(data.get("subjects", ()))[:10]

        covers = data.get("covers", [])
        images: list[ProviderImage] = []
        for cov_id in covers:
            if isinstance(cov_id, int) and cov_id > 0:
                images.append(
                    ProviderImage(
                        kind="poster",
                        source_url=f"{self.covers_base_url}/b/id/{cov_id}-L.jpg",
                        is_primary=len(images) == 0,
                    )
                )

        return BookMetadata(
            provider=self.name,
            provider_id=book_id,
            title=title,
            overview=overview,
            genres=subjects,
            images=tuple(images),
            raw=data,
        )

    async def fetch_book_images(self, book_id: str) -> list[ProviderImage]:
        details = await self.fetch_book_details(book_id)
        return list(details.images) if details else []

    async def close(self) -> None:
        await self.dispose()
