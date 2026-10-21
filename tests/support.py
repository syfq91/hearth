"""Reusable fakes for metadata tests."""

from collections.abc import Sequence

from app.services.metadata.base import (
    MetadataProvider,
    MovieMetadata,
    ProviderImage,
    SearchResult,
)


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


class FakeArtwork:
    """Record artwork handed to the image pipeline."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, list[ProviderImage]]] = []

    async def __call__(self, item_id: str, provider: str, images: Sequence[ProviderImage]) -> int:
        self.calls.append((item_id, provider, list(images)))
        return len(images)
