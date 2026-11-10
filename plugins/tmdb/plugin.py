"""TMDB metadata provider plugin for movies and TV series."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.plugins.base import MetadataPlugin, PluginContext, PluginStatus
from app.core.plugins.manifest import PluginManifest
from app.services.metadata.base import (
    EpisodeMetadata,
    MovieMetadata,
    ProviderImage,
    SearchResult,
    SeasonMetadata,
    SeriesMetadata,
)
from plugins.tmdb.client import TMDBClient


class TMDBConfig(BaseModel):
    """Configuration schema for TheMovieDb plugin."""

    api_key: str = Field(
        default="",
        description="TMDB v3 API Key or v4 Read Access Token",
    )
    include_adult: bool = Field(
        default=False,
        description="Whether to include adult titles in search results",
    )
    preferred_language: str = Field(
        default="en-US",
        description="ISO 639-1 language code for metadata and descriptions",
    )


class TMDBPlugin(MetadataPlugin[TMDBConfig]):
    """Plugin providing movie and TV series metadata from TMDB."""

    name = "tmdb"

    def __init__(
        self,
        context: PluginContext,
        manifest: PluginManifest,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(context, manifest)
        self._custom_client = client
        self._tmdb_client: TMDBClient | None = None

    @property
    def supported_media_types(self) -> tuple[str, ...]:
        return ("movie", "series", "tv")

    @property
    def enabled(self) -> bool:
        if self.status != PluginStatus.ACTIVE:
            return False
        if self.config is not None and self.config.api_key:
            return True
        return bool(self.context.settings.tmdb_api_key)

    def apply_configuration(self, config_data: dict[str, Any]) -> None:
        data = dict(config_data)
        # Fall back to legacy environment setting if api_key is not set in DB
        if not data.get("api_key") and self.context.settings.tmdb_api_key:
            data["api_key"] = self.context.settings.tmdb_api_key

        self.config = TMDBConfig(**data)
        if self._tmdb_client is not None:
            self._tmdb_client.api_key = self.config.api_key
            self._tmdb_client.include_adult = self.config.include_adult
            self._tmdb_client.preferred_language = self.config.preferred_language

    async def initialize(self) -> None:
        if self.config is None:
            self.apply_configuration({})

        api_key = self.config.api_key if self.config else ""
        self._tmdb_client = TMDBClient(
            api_key=api_key,
            sessionmaker=self.context.sessionmaker,
            client=self._custom_client,
            base_url=self.context.settings.tmdb_base_url,
            image_base_url=self.context.settings.tmdb_image_base_url,
            timeout_seconds=self.context.settings.tmdb_timeout_seconds,
            min_interval_seconds=self.context.settings.tmdb_min_interval_seconds,
            cache_ttl_hours=self.context.settings.tmdb_cache_ttl_hours,
            include_adult=self.config.include_adult if self.config else False,
            preferred_language=self.config.preferred_language if self.config else "en-US",
            provider_name=self.name,
        )

    async def dispose(self) -> None:
        if self._tmdb_client is not None:
            await self._tmdb_client.close()
            self._tmdb_client = None

    def _ensure_client(self) -> TMDBClient:
        if self._tmdb_client is None:
            # Lazy initialize if accessed before explicit lifecycle initialize
            api_key = (
                self.config.api_key if self.config else (self.context.settings.tmdb_api_key or "")
            )
            self._tmdb_client = TMDBClient(
                api_key=api_key,
                sessionmaker=self.context.sessionmaker,
                client=self._custom_client,
                base_url=self.context.settings.tmdb_base_url,
                image_base_url=self.context.settings.tmdb_image_base_url,
                timeout_seconds=self.context.settings.tmdb_timeout_seconds,
                min_interval_seconds=self.context.settings.tmdb_min_interval_seconds,
                cache_ttl_hours=self.context.settings.tmdb_cache_ttl_hours,
                include_adult=self.config.include_adult if self.config else False,
                preferred_language=self.config.preferred_language if self.config else "en-US",
                provider_name=self.name,
            )
        return self._tmdb_client

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        return await self._ensure_client().search(query, year)

    async def fetch_details(self, provider_id: str) -> MovieMetadata | None:
        return await self._ensure_client().fetch_details(provider_id)

    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        return await self._ensure_client().fetch_images(provider_id)

    async def search_tv(self, query: str, year: int | None = None) -> list[SearchResult]:
        return await self._ensure_client().search_tv(query, year)

    async def fetch_tv_details(self, series_id: str) -> SeriesMetadata | None:
        return await self._ensure_client().fetch_tv_details(series_id)

    async def fetch_tv_season(self, series_id: str, season_number: int) -> SeasonMetadata | None:
        return await self._ensure_client().fetch_tv_season(series_id, season_number)

    async def fetch_tv_episode(
        self, series_id: str, season_number: int, episode_number: int
    ) -> EpisodeMetadata | None:
        return await self._ensure_client().fetch_tv_episode(
            series_id, season_number, episode_number
        )

    async def fetch_tv_images(self, series_id: str) -> list[ProviderImage]:
        return await self._ensure_client().fetch_tv_images(series_id)

    async def close(self) -> None:
        await self.dispose()
