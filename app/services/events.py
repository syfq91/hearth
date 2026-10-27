"""In-memory pub/sub event broadcaster for real-time events."""

import asyncio
from typing import Any


class EventBroadcaster:
    """Lightweight in-memory event broadcaster distributing events to active SSE connections."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        """Publish an event to all subscribers without blocking."""
        dead_queues: list[asyncio.Queue[dict[str, Any]]] = []
        payload = {"event": event_type, "data": data}
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead_queues.append(q)
            except Exception:
                dead_queues.append(q)

        for dq in dead_queues:
            self._subscribers.discard(dq)


_GLOBAL_BROADCASTER = EventBroadcaster()


def get_broadcaster() -> EventBroadcaster:
    """Return the global event broadcaster instance."""
    return _GLOBAL_BROADCASTER
