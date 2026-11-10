"""Dynamic registry for local and plugin-provided metadata providers."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Setting
from app.services.metadata.base import MetadataProvider

logger = logging.getLogger("hearth.metadata.registry")

LIBRARY_PROVIDERS_KEY_PREFIX = "library."
LIBRARY_PROVIDERS_KEY_SUFFIX = ".metadata_providers"

# Canonical media types
TYPE_MOVIE = "movie"
TYPE_SERIES = "series"
TYPE_TV = "tv"
TYPE_ARTIST = "artist"
TYPE_ALBUM = "album"
TYPE_MUSIC = "music"
TYPE_BOOK = "book"

# Default supported types for well-known built-in providers
DEFAULT_PROVIDER_TYPES: dict[str, tuple[str, ...]] = {
    "local": (TYPE_MOVIE, TYPE_SERIES, TYPE_TV, TYPE_ARTIST, TYPE_ALBUM, TYPE_MUSIC, TYPE_BOOK),
    "tmdb": (TYPE_MOVIE, TYPE_SERIES, TYPE_TV),
    "musicbrainz": (TYPE_ARTIST, TYPE_ALBUM, TYPE_MUSIC),
    "openlibrary": (TYPE_BOOK,),
    "googlebooks": (TYPE_BOOK,),
}


def _normalize_type(media_type: str) -> str:
    """Normalize aliases like tv -> series, music -> album/artist."""
    t = media_type.lower()
    if t == "tv":
        return TYPE_SERIES
    return t


def get_library_setting_key(library_id: str) -> str:
    """Return the database setting key for a library's provider priority."""
    return f"{LIBRARY_PROVIDERS_KEY_PREFIX}{library_id}{LIBRARY_PROVIDERS_KEY_SUFFIX}"


async def load_library_provider_order(
    sessionmaker: async_sessionmaker[AsyncSession],
    library_id: str,
) -> list[str] | None:
    """Load customized provider priority order for a specific library."""
    key = get_library_setting_key(library_id)
    async with sessionmaker() as session:
        row = await session.get(Setting, key)
        if row is None:
            return None
        try:
            data = json.loads(row.value)
            if isinstance(data, list) and all(isinstance(x, str) for x in data):
                return data
        except (TypeError, ValueError):
            pass
        return None


async def save_library_provider_order(
    sessionmaker: async_sessionmaker[AsyncSession],
    library_id: str,
    provider_names: Sequence[str] | None,
) -> None:
    """Save or clear customized provider priority order for a specific library."""
    key = get_library_setting_key(library_id)
    async with sessionmaker() as session:
        row = await session.get(Setting, key)
        if provider_names is None:
            if row is not None:
                await session.delete(row)
                await session.commit()
            return

        encoded = json.dumps(list(provider_names))
        if row is None:
            session.add(Setting(key=key, value=encoded))
        else:
            row.value = encoded
        await session.commit()


class MetadataRegistry:
    """Holds active metadata providers and resolves prioritized provider lists."""

    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession] | None = None,
        providers: Sequence[MetadataProvider] | None = None,
    ) -> None:
        self.sessionmaker = sessionmaker
        self._providers: dict[str, MetadataProvider] = {}
        self._types: dict[str, set[str]] = {}
        self._cached_library_orders: dict[str, list[str] | None] = {}

        if providers:
            for p in providers:
                self.register(p)

    def register(
        self,
        provider: MetadataProvider,
        supported_types: Sequence[str] | None = None,
    ) -> None:
        """Register a metadata provider and its supported media types."""
        self._providers[provider.name] = provider

        if supported_types is not None:
            types = set(_normalize_type(t) for t in supported_types)
        elif hasattr(provider, "supported_media_types"):
            types = set(
                _normalize_type(t)
                for t in getattr(provider, "supported_media_types")  # noqa: B009
            )
        else:
            types = set(
                DEFAULT_PROVIDER_TYPES.get(
                    provider.name,
                    (TYPE_MOVIE, TYPE_SERIES, TYPE_ARTIST, TYPE_ALBUM, TYPE_BOOK),
                )
            )

        self._types[provider.name] = types
        logger.debug(
            "Registered metadata provider",
            extra={"provider": provider.name, "supported_types": list(types)},
        )

    def unregister(self, name: str) -> MetadataProvider | None:
        """Unregister a provider by name."""
        self._types.pop(name, None)
        return self._providers.pop(name, None)

    def get_provider(self, name: str) -> MetadataProvider | None:
        """Retrieve a registered provider by name."""
        return self._providers.get(name)

    def all_providers(self) -> list[MetadataProvider]:
        """Return all registered providers."""
        return list(self._providers.values())

    def get_providers_for(
        self,
        media_type: str,
        *,
        library_id: str | None = None,
        priority_order: Sequence[str] | None = None,
    ) -> list[MetadataProvider]:
        """Return registered providers matching media_type, ordered by priority.

        Order precedence:
        1. Explicit priority_order argument if passed.
        2. Cached or configured library-level priority order if library_id is given.
        3. Default registration order (with local provider preserving its position).
        """
        norm_type = _normalize_type(media_type)
        matching = [
            p
            for p in self._providers.values()
            if norm_type in self._types.get(p.name, set()) and p.enabled
        ]

        # Determine effective order list
        order_names: Sequence[str] | None = priority_order
        if order_names is None and library_id is not None:
            order_names = self._cached_library_orders.get(library_id)

        if not order_names:
            return matching

        # Sort matching providers according to order_names
        order_map = {name: index for index, name in enumerate(order_names)}
        # Providers not explicitly in order_names appear at the end, preserving order
        return sorted(matching, key=lambda p: order_map.get(p.name, 999))

    async def get_or_load_library_order(self, library_id: str) -> list[str] | None:
        """Fetch library-level order from cache or database."""
        if library_id in self._cached_library_orders:
            return self._cached_library_orders[library_id]
        if self.sessionmaker is not None:
            order = await load_library_provider_order(self.sessionmaker, library_id)
            self._cached_library_orders[library_id] = order
            return order
        return None

    async def prime_library_orders(self) -> None:
        """Cache all library-level provider orders from the database."""
        if self.sessionmaker is None:
            return

        prefix = LIBRARY_PROVIDERS_KEY_PREFIX
        suffix = LIBRARY_PROVIDERS_KEY_SUFFIX
        async with self.sessionmaker() as session:
            query = select(Setting).where(Setting.key.like(f"{prefix}%{suffix}"))
            rows = await session.scalars(query)
            for row in rows:
                lib_id = row.key[len(prefix) : -len(suffix)]
                try:
                    data = json.loads(row.value)
                    if isinstance(data, list) and all(isinstance(x, str) for x in data):
                        self._cached_library_orders[lib_id] = data
                except (TypeError, ValueError):
                    pass
