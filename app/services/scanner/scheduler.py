"""Periodic scan scheduling.

The scheduler runs in the worker process. It enqueues at most one scan per
library, based on ``Library.last_scan_at``, so API and worker restarts do not
cause duplicate work.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import Library
from app.services.scanner.jobs import enqueue_scan

logger = logging.getLogger("hearth.scanner.scheduler")


class ScanScheduler:
    """Enqueue due library scans on a fixed interval."""

    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.clock = clock

    async def tick(self) -> list[str]:
        """Enqueue scans for libraries whose last scan is older than the interval."""
        now = self.clock()
        interval = timedelta(hours=self.settings.scan_interval_hours)
        created: list[str] = []

        async with self.sessionmaker() as session:
            libraries = list(
                await session.scalars(
                    select(Library)
                    .where(Library.type == "movie", Library.paths.any())
                    .order_by(Library.id)
                )
            )
            for library in libraries:
                if library.last_scan_at is not None and now - library.last_scan_at < interval:
                    continue
                job, is_new = await enqueue_scan(session, library.id)
                if is_new:
                    created.append(job.id)
            await session.commit()

        if created:
            logger.info("queued scheduled scans", extra={"count": len(created)})
        return created

    async def run(self, stop: asyncio.Event) -> None:
        """Tick until ``stop`` is set, waking often enough to observe shutdown."""
        logger.info(
            "scan scheduler started",
            extra={"interval_hours": self.settings.scan_interval_hours},
        )
        try:
            while not stop.is_set():
                try:
                    await self.tick()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - a failing tick must not kill the worker
                    logger.exception("scan scheduler tick failed")
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        stop.wait(), timeout=self.settings.scan_scheduler_wake_seconds
                    )
        finally:
            logger.info("scan scheduler stopped")
