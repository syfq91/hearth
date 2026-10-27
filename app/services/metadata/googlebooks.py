"""Google Books metadata provider fallback for books."""

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

logger = logging.getLogger("hearth.metadata.googlebooks")


class GoogleBooksProvider(MetadataProvider):
    """Google Books provider as fallback for books missing from Open Library."""

    name = "googlebooks"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.base_url = settings.google_books_base_url.rstrip("/")
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            timeout=10.0,
            headers={"User-Agent": "HearthMediaServer/0.1.0"},
        )

    @property
    def enabled(self) -> bool:
        return self.settings.google_books_enabled

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
        """Search Google Books volumes."""
        if not self.enabled:
            return []

        if isbn:
            q = f"isbn:{isbn.replace('-', '').strip()}"
        else:
            q = f"intitle:{query}"
            if author:
                q += f"+inauthor:{author}"

        params = {"q": q, "maxResults": "10"}
        try:
            res = await self._client.get(f"{self.base_url}/volumes", params=params)
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

        try:
            res = await self._client.get(f"{self.base_url}/volumes/{book_id}")
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
        if self._owned_client:
            await self._client.aclose()
