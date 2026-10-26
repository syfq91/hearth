"""Metadata provider interface and typed result models."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderError(Exception):
    """A provider call failed; the item should stay retryable."""


class ProviderUnavailable(ProviderError):
    """The provider is not configured or rejected its credentials."""


@dataclass(frozen=True)
class SearchResult:
    """One candidate returned by a provider search."""

    provider: str
    provider_id: str
    title: str
    year: int | None = None
    overview: str | None = None
    poster_url: str | None = None
    backdrop_url: str | None = None
    popularity: float = 0.0


@dataclass(frozen=True)
class PersonCredit:
    """A cast or crew credit."""

    name: str
    role: str
    character: str | None = None
    ordering: int = 0
    provider_id: str | None = None


@dataclass(frozen=True)
class ProviderImage:
    """Artwork that can be downloaded or copied into the image cache."""

    kind: str
    source_url: str | None = None
    file_path: str | None = None
    width: int | None = None
    height: int | None = None
    is_primary: bool = False
    language: str | None = None


@dataclass(frozen=True)
class MovieMetadata:
    """Normalized movie details from any provider."""

    provider: str
    provider_id: str
    title: str
    year: int | None = None
    original_title: str | None = None
    release_date: str | None = None
    overview: str | None = None
    tagline: str | None = None
    runtime_seconds: int | None = None
    rating: float | None = None
    genres: tuple[str, ...] = ()
    credits: tuple[PersonCredit, ...] = ()
    provider_ids: dict[str, str] = field(default_factory=dict)
    images: tuple[ProviderImage, ...] = ()
    sidecar_subtitles: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class EpisodeMetadata:
    """Normalized episode details from any provider."""

    provider: str
    provider_id: str
    series_provider_id: str = ""
    season_number: int | None = None
    episode_number: int | None = None
    title: str = ""
    overview: str | None = None
    air_date: str | None = None
    runtime_seconds: int | None = None
    rating: float | None = None
    still_url: str | None = None
    credits: tuple[PersonCredit, ...] = ()
    provider_ids: dict[str, str] = field(default_factory=dict)
    images: tuple[ProviderImage, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class SeasonMetadata:
    """Normalized season details from any provider."""

    provider: str
    provider_id: str | None = None
    series_provider_id: str = ""
    season_number: int = 1
    title: str | None = None
    overview: str | None = None
    air_date: str | None = None
    poster_url: str | None = None
    episode_count: int | None = None
    episodes: tuple[EpisodeMetadata, ...] = ()
    images: tuple[ProviderImage, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class SeriesMetadata:
    """Normalized TV series details from any provider."""

    provider: str
    provider_id: str
    title: str
    year: int | None = None
    original_title: str | None = None
    first_air_date: str | None = None
    overview: str | None = None
    status: str | None = None
    network: str | None = None
    rating: float | None = None
    genres: tuple[str, ...] = ()
    credits: tuple[PersonCredit, ...] = ()
    provider_ids: dict[str, str] = field(default_factory=dict)
    images: tuple[ProviderImage, ...] = ()
    seasons: tuple[SeasonMetadata, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


class MetadataProvider(ABC):
    """The small provider surface the matcher depends on."""

    name: str

    @abstractmethod
    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        """Search for movie candidates."""

    @abstractmethod
    async def fetch_details(self, provider_id: str) -> MovieMetadata | None:
        """Fetch normalized details for one candidate."""

    @abstractmethod
    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        """Fetch artwork candidates for one candidate."""

    async def search_tv(self, query: str, year: int | None = None) -> list[SearchResult]:
        """Search for TV series candidates."""
        return []

    async def fetch_tv_details(self, series_id: str) -> SeriesMetadata | None:
        """Fetch normalized TV series details."""
        return None

    async def fetch_tv_season(self, series_id: str, season_number: int) -> SeasonMetadata | None:
        """Fetch normalized TV season details including episode list."""
        return None

    async def fetch_tv_episode(
        self, series_id: str, season_number: int, episode_number: int
    ) -> EpisodeMetadata | None:
        """Fetch normalized TV episode details."""
        return None

    async def fetch_tv_images(self, series_id: str) -> list[ProviderImage]:
        """Fetch artwork candidates for a TV series."""
        return []

    async def close(self) -> None:
        """Release provider resources."""
        return None
