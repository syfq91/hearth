"""Filesystem watcher that turns debounced events into targeted scans.

Only local media libraries are watched. ``watchfiles`` coalesces bursts of
events over ``scan_watch_debounce_seconds``; each batch is grouped by library
and enqueued as one scan job whose ``targets`` are the affected directories.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from watchfiles import Change, awatch

from app.core.config import Settings
from app.db.models import Library, LibraryPath
from app.services.scanner.jobs import enqueue_scan
from app.services.scanner.walker import VIDEO_EXTENSIONS

logger = logging.getLogger("hearth.scanner.watcher")

WATCH_EXTENSIONS = VIDEO_EXTENSIONS | {
    ".nfo",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".srt",
    ".ass",
    ".ssa",
    ".vtt",
    ".sub",
}

WatchFn = Callable[..., AsyncIterator[set[tuple[Change, str]]]]


class LibraryWatcher:
    """Watch movie library roots and enqueue scans for changed directories."""

    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        watch: WatchFn | None = None,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self._watch: WatchFn = watch or awatch

    async def _roots(self) -> dict[str, str]:
        """Return existing library roots mapped to their library id."""
        async with self.sessionmaker() as session:
            rows = await session.execute(
                select(LibraryPath.path, Library.id)
                .join(Library, Library.id == LibraryPath.library_id)
                .where(Library.type == "movie")
            )
            roots: dict[str, str] = {}
            for raw_path, library_id in rows.all():
                path = os.path.normpath(os.path.abspath(raw_path))
                if not os.path.isdir(path):
                    continue
                existing = roots.get(path)
                if existing is not None and existing != library_id:
                    logger.warning(
                        "library root shared by multiple libraries", extra={"path": path}
                    )
                    continue
                roots[path] = library_id
            return roots

    @staticmethod
    def _library_for(path: str, roots: dict[str, str]) -> str | None:
        best: tuple[str, str] | None = None
        for root, library_id in roots.items():
            if (path == root or path.startswith(root + os.sep)) and (
                best is None or len(root) > len(best[0])
            ):
                best = (root, library_id)
        return best[1] if best is not None else None

    async def handle_changes(self, changes: Iterable[tuple[Change, str]]) -> list[str]:
        """Group one debounced batch by library and enqueue targeted scans."""
        roots = await self._roots()
        grouped: dict[str, set[str]] = defaultdict(set)
        for _change, raw_path in changes:
            path = os.path.normpath(os.path.abspath(str(raw_path)))
            if Path(path).suffix.casefold() not in WATCH_EXTENSIONS:
                continue
            library_id = self._library_for(path, roots)
            if library_id is None:
                continue
            target = path if os.path.isdir(path) else os.path.dirname(path)
            grouped[library_id].add(target or path)

        if not grouped:
            return []

        job_ids: list[str] = []
        async with self.sessionmaker() as session:
            for library_id, targets in grouped.items():
                job, created = await enqueue_scan(session, library_id, targets=sorted(targets))
                if created:
                    job_ids.append(job.id)
            await session.commit()

        if job_ids:
            logger.info(
                "queued watcher scans",
                extra={"libraries": len(grouped), "jobs": len(job_ids)},
            )
        return job_ids

    async def run(self, stop: asyncio.Event) -> None:
        """Watch roots until ``stop`` is set, refreshing roots periodically."""
        logger.info(
            "library watcher started",
            extra={"debounce_seconds": self.settings.scan_watch_debounce_seconds},
        )
        debounce_ms = max(1, int(self.settings.scan_watch_debounce_seconds * 1000))
        refresh_ms = max(1, int(self.settings.scan_watch_refresh_seconds * 1000))
        try:
            while not stop.is_set():
                roots = await self._roots()
                if not roots:
                    await self._sleep(stop, self.settings.scan_watch_refresh_seconds)
                    continue

                snapshot = set(roots)
                thread_stop = threading.Event()
                bridge = asyncio.create_task(self._bridge(stop, thread_stop))
                try:
                    async for changes in self._watch(
                        *(Path(root) for root in roots),
                        debounce=debounce_ms,
                        step=50,
                        stop_event=thread_stop,
                        rust_timeout=refresh_ms,
                        yield_on_timeout=True,
                    ):
                        if stop.is_set():
                            break
                        if changes:
                            await self.handle_changes(changes)
                        # Pick up libraries added or roots unmounted since the
                        # generator started; restarting is the only way to
                        # change watchfiles' path set.
                        if set(await self._roots()) != snapshot:
                            break
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - watcher failures are recoverable
                    logger.exception("library watcher stopped unexpectedly; retrying")
                    await self._sleep(stop, 5.0)
                finally:
                    thread_stop.set()
                    bridge.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await bridge
        finally:
            logger.info("library watcher stopped")

    @staticmethod
    async def _bridge(stop: asyncio.Event, thread_stop: threading.Event) -> None:
        await stop.wait()
        thread_stop.set()

    @staticmethod
    async def _sleep(stop: asyncio.Event, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=max(0.1, seconds))
