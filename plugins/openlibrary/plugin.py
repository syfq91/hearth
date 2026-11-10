"""Open Library metadata provider plugin for books and covers."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.plugins.base import MetadataPlugin, PluginContext
from app.core.plugins.manifest import PluginManifest
from app.services.metadata.base import (
    BookMetadata,
    MovieMetadata,
    ProviderError,
    ProviderImage,
    SearchResult,
)

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

    @property
    def supported_media_types(self) -> tuple[str, ...]:
        return ("book",)

    @property
    def enabled(self) -> bool:
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

        client = self._ensure_client()
        try:
            res = await client.get(f"{self.base_url}/search.json", params=params)
            if res.status_code != 200:
                logger.warning("Open Library search returned status %s", res.status_code)
                return []
            data = res.json()
        except Exception as exc:
            logger.warning("Open Library search failed: %s", exc)
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

        client = self._ensure_client()
        try:
            res = await client.get(f"{self.base_url}{path}")
            if res.status_code == 404:
                return None
            if res.status_code != 200:
                raise ProviderError(f"Open Library details failed: HTTP {res.status_code}")
            data = res.json()
        except Exception as exc:
            logger.warning("Open Library detail lookup failed for %s: %s", book_id, exc)
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
