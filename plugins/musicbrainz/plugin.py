"""MusicBrainz metadata provider plugin for artists and albums."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.core.plugins.base import MetadataPlugin, PluginContext
from app.core.plugins.manifest import PluginManifest
from app.services.metadata.base import (
    AlbumMetadata,
    ArtistMetadata,
    ProviderImage,
    SearchResult,
)
from plugins.musicbrainz.client import MusicBrainzClient


class MusicBrainzConfig(BaseModel):
    """Configuration schema for MusicBrainz plugin."""

    enabled: bool = Field(
        default=True,
        description="Whether MusicBrainz metadata lookups are enabled",
    )
    rate_limit_seconds: float = Field(
        default=1.0,
        description="Minimum seconds between consecutive outbound requests",
    )


class MusicBrainzPlugin(MetadataPlugin[MusicBrainzConfig]):
    """Plugin providing artist and release group metadata from MusicBrainz."""

    name = "musicbrainz"

    def __init__(
        self,
        context: PluginContext,
        manifest: PluginManifest,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(context, manifest)
        self._custom_client = client
        self._mb_client: MusicBrainzClient | None = None

    @property
    def supported_media_types(self) -> tuple[str, ...]:
        return ("artist", "album", "music")

    @property
    def enabled(self) -> bool:
        if self.config is not None:
            return self.config.enabled
        return self.context.settings.musicbrainz_enabled

    def apply_configuration(self, config_data: dict[str, Any]) -> None:
        data = dict(config_data)
        if "enabled" not in data:
            data["enabled"] = self.context.settings.musicbrainz_enabled
        if "rate_limit_seconds" not in data:
            data["rate_limit_seconds"] = self.context.settings.musicbrainz_rate_limit_seconds

        self.config = MusicBrainzConfig(**data)
        if self._mb_client is not None:
            self._mb_client.enabled = self.config.enabled
            self._mb_client.rate_limit_seconds = self.config.rate_limit_seconds

    async def initialize(self) -> None:
        if self.config is None:
            self.apply_configuration({})

        rate_limit = (
            self.config.rate_limit_seconds
            if self.config
            else self.context.settings.musicbrainz_rate_limit_seconds
        )
        is_enabled = (
            self.config.enabled if self.config else self.context.settings.musicbrainz_enabled
        )

        self._mb_client = MusicBrainzClient(
            sessionmaker=self.context.sessionmaker,
            enabled=is_enabled,
            client=self._custom_client,
            base_url=self.context.settings.musicbrainz_base_url,
            cover_art_archive_base_url=self.context.settings.cover_art_archive_base_url,
            user_agent=self.context.settings.musicbrainz_user_agent,
            timeout_seconds=self.context.settings.musicbrainz_timeout_seconds,
            rate_limit_seconds=rate_limit,
            cache_ttl_hours=self.context.settings.musicbrainz_cache_ttl_hours,
            provider_name=self.name,
        )

    async def dispose(self) -> None:
        if self._mb_client is not None:
            await self._mb_client.close()
            self._mb_client = None

    def _ensure_client(self) -> MusicBrainzClient:
        if self._mb_client is None:
            rate_limit = (
                self.config.rate_limit_seconds
                if self.config
                else self.context.settings.musicbrainz_rate_limit_seconds
            )
            is_enabled = (
                self.config.enabled if self.config else self.context.settings.musicbrainz_enabled
            )
            self._mb_client = MusicBrainzClient(
                sessionmaker=self.context.sessionmaker,
                enabled=is_enabled,
                client=self._custom_client,
                base_url=self.context.settings.musicbrainz_base_url,
                cover_art_archive_base_url=self.context.settings.cover_art_archive_base_url,
                user_agent=self.context.settings.musicbrainz_user_agent,
                timeout_seconds=self.context.settings.musicbrainz_timeout_seconds,
                rate_limit_seconds=rate_limit,
                cache_ttl_hours=self.context.settings.musicbrainz_cache_ttl_hours,
                provider_name=self.name,
            )
        return self._mb_client

    async def search_artist(self, query: str) -> list[SearchResult]:
        return await self._ensure_client().search_artist(query)

    async def fetch_artist_details(self, artist_id: str) -> ArtistMetadata | None:
        return await self._ensure_client().fetch_artist_details(artist_id)

    async def search_album(self, query: str, artist: str | None = None) -> list[SearchResult]:
        return await self._ensure_client().search_album(query, artist=artist)

    async def fetch_album_details(self, album_id: str) -> AlbumMetadata | None:
        return await self._ensure_client().fetch_album_details(album_id)

    async def fetch_album_images(self, album_id: str) -> list[ProviderImage]:
        return await self._ensure_client().fetch_album_images(album_id)

    async def close(self) -> None:
        await self.dispose()
