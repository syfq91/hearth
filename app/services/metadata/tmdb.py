"""Backward compatibility adapter for TMDB metadata provider.

The core implementation has moved to `plugins/tmdb/`. This module remains
to preserve existing imports and constructor signatures.
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.metadata.base import MetadataProvider
from plugins.tmdb.client import TMDBClient


class TMDBProvider(TMDBClient, MetadataProvider):
    """Backward-compatible adapter for TMDB metadata provider."""

    name = "tmdb"

    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        super().__init__(
            api_key=settings.tmdb_api_key or "",
            sessionmaker=sessionmaker,
            client=client,
            base_url=settings.tmdb_base_url,
            image_base_url=settings.tmdb_image_base_url,
            timeout_seconds=settings.tmdb_timeout_seconds,
            min_interval_seconds=settings.tmdb_min_interval_seconds,
            cache_ttl_hours=settings.tmdb_cache_ttl_hours,
            provider_name=self.name,
        )
