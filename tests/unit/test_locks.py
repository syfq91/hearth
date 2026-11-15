"""Unit tests for process-wide locks that must survive event-loop changes."""

import asyncio

from app.core.locks import LoopAwareLock


def test_lock_serializes_contended_acquires() -> None:
    lock = LoopAwareLock()

    async def main() -> list[tuple[str, int]]:
        events: list[tuple[str, int]] = []

        async def worker(index: int) -> None:
            async with lock:
                events.append(("start", index))
                await asyncio.sleep(0.01)
                events.append(("end", index))

        await asyncio.gather(*(worker(i) for i in range(3)))
        return events

    events = asyncio.run(main())
    assert [kind for kind, _ in events] == ["start", "end"] * 3


def test_lock_survives_a_new_event_loop() -> None:
    """A plain asyncio.Lock raises "bound to a different event loop" here."""
    lock = LoopAwareLock()

    async def contention_round() -> list[int]:
        order: list[int] = []

        async def worker(index: int) -> None:
            async with lock:
                order.append(index)
                await asyncio.sleep(0)

        await asyncio.gather(*(worker(i) for i in range(4)))
        return order

    assert asyncio.run(contention_round()) == [0, 1, 2, 3]
    # A second loop must be able to contend on the same process-wide lock.
    assert asyncio.run(contention_round()) == [0, 1, 2, 3]
