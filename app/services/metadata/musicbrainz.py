"""Backward compatibility adapter for MusicBrainz metadata provider.

The core implementation has moved to `plugins/musicbrainz/`. This module remains
to preserve existing imports and constructor signatures.
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.metadata.base import MetadataProvider
from plugins.musicbrainz.client import MusicBrainzClient


class MusicBrainzProvider(MusicBrainzClient, MetadataProvider):
    """Backward-compatible adapter for MusicBrainz metadata provider."""

    name = "musicbrainz"

    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        super().__init__(
            sessionmaker=sessionmaker,
            enabled=settings.musicbrainz_enabled,
            client=client,
            base_url=settings.musicbrainz_base_url,
            cover_art_archive_base_url=settings.cover_art_archive_base_url,
            user_agent=settings.musicbrainz_user_agent,
            timeout_seconds=settings.musicbrainz_timeout_seconds,
            rate_limit_seconds=settings.musicbrainz_rate_limit_seconds,
            cache_ttl_hours=settings.musicbrainz_cache_ttl_hours,
            provider_name=self.name,
        )
