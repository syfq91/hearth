"""Open Library metadata provider for books and covers."""

from __future__ import annotations

import logging

import httpx

from app.core.config import Settings
from app.services.metadata.base import (
    BookMetadata,
    MetadataProvider,
    MovieMetadata,
    ProviderError,
    ProviderImage,
    SearchResult,
)

logger = logging.getLogger("hearth.metadata.openlibrary")


class OpenLibraryProvider(MetadataProvider):
    """Metadata provider searching Open Library and fetching book details."""

    name = "openlibrary"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = settings.openlibrary_base_url.rstrip("/")
        self.covers_base_url = settings.openlibrary_covers_base_url.rstrip("/")
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
            headers={"User-Agent": "HearthMediaServer/0.1.0"},
        )

    @property
    def enabled(self) -> bool:
        return self.settings.openlibrary_enabled

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        return []

    async def fetch_details(self, provider_id: str) -> MovieMetadata | None:
        return None

    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        return []

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

        try:
            res = await self._client.get(f"{self.base_url}/search.json", params=params)
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

        try:
            res = await self._client.get(f"{self.base_url}{path}")
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
        if self._owned_client:
            await self._client.aclose()
