"""Process-wide locks that stay usable across event loops.

State such as the playback write lock is intentionally shared for the whole
process, but the ``asyncio`` primitive cannot be: a module-level
``asyncio.Lock`` binds itself to the first loop that *contends* on it and then
raises ``RuntimeError`` from any other loop. That happens in practice when an
ASGI lifespan is restarted (uvicorn reload) or when a test session drives the
same module from fresh loops. It is invisible while the lock is uncontended,
because only the contended path inspects the bound loop.
"""

from __future__ import annotations

import asyncio
from types import TracebackType


class LoopAwareLock:
    """A lock whose underlying primitive follows the running event loop."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _for_running_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            # Two loops never run concurrently in one thread, so replacing the
            # primitive cannot drop a held lock.
            self._lock = asyncio.Lock()
            self._loop = loop
        return self._lock

    async def __aenter__(self) -> None:
        await self._for_running_loop().acquire()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        lock = self._lock
        if lock is not None and lock.locked():
            lock.release()

    @property
    def locked(self) -> bool:
        """Whether the current loop's lock is held."""
        lock = self._lock
        return bool(lock is not None and lock.locked())


__all__ = ["LoopAwareLock"]
