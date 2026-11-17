"""Reusable response cache and rate limiter for lightweight provider plugins."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.base import utcnow
from app.db.models import ProviderCache

DEFAULT_CACHE_TTL_HOURS = 168.0


async def cache_get(
    sessionmaker: async_sessionmaker[AsyncSession] | None,
    provider: str,
    key: str,
) -> dict[str, Any] | None:
    """Return a fresh cached payload, or ``None`` when absent/expired."""
    if sessionmaker is None:
        return None
    async with sessionmaker() as session:
        entry = await session.get(ProviderCache, (provider, key))
        if entry is None or entry.expires_at <= utcnow():
            return None
        return entry.payload if isinstance(entry.payload, dict) else None


async def cache_set(
    sessionmaker: async_sessionmaker[AsyncSession] | None,
    provider: str,
    key: str,
    payload: dict[str, Any],
    *,
    ttl_hours: float = DEFAULT_CACHE_TTL_HOURS,
) -> None:
    """Persist a provider payload in the shared cache."""
    if sessionmaker is None:
        return
    now = utcnow()
    async with sessionmaker() as session:
        await session.merge(
            ProviderCache(
                provider=provider,
                key=key,
                payload=payload,
                fetched_at=now,
                expires_at=now + timedelta(hours=ttl_hours),
            )
        )
        await session.commit()


class RateLimiter:
    """Reserve start slots under a lock without sleeping while holding it."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        if self.interval <= 0:
            return
        loop = asyncio.get_running_loop()
        async with self._lock:
            next_allowed = self._last + self.interval
            wait = max(0.0, next_allowed - loop.time())
            self._last = next_allowed
        if wait:
            await asyncio.sleep(wait)
