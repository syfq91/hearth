"""In-memory sliding-window rate limiting for login attempts."""

import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    """Track timestamps per key and report when the window is saturated.

    The limiter is process-local by design: a single-node home server needs no
    shared store, and losing counters on restart is acceptable for login
    throttling. Operations contain no ``await`` points, so concurrent requests
    cannot interleave inside a check/hit pair.
    """

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events.get(key)
        if events is None:
            events = deque()
            self._events[key] = events
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if not events:
            self._events.pop(key, None)
            events = deque()
            self._events[key] = events
        return events

    def retry_after(self, key: str) -> float:
        """Return seconds until the key may retry, or 0 when allowed now."""
        now = self._clock()
        events = self._prune(key, now)
        if len(events) < self.limit:
            return 0.0
        return max(0.0, (events[0] + self.window_seconds) - now)

    def hit(self, key: str) -> None:
        """Record one failed attempt for a key."""
        now = self._clock()
        events = self._prune(key, now)
        events.append(now)

    def reset(self, key: str) -> None:
        """Clear a key after a successful authentication."""
        self._events.pop(key, None)

    def clear(self) -> None:
        """Drop all counters (used by tests)."""
        self._events.clear()
