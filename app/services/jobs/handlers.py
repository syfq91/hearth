"""Job handler registry and the built-in no-op test job."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


class JobCancelled(Exception):
    """Raised by a handler when it observes a cancellation request."""


@dataclass
class JobContext:
    """Everything a handler needs without exposing the worker internals."""

    job_id: str
    payload: dict[str, Any]
    report_progress: Callable[[float], Awaitable[None]]
    is_cancelled: Callable[[], Awaitable[bool]]


JobHandler = Callable[[JobContext], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class JobSpec:
    """Handler plus its per-type concurrency limit."""

    handler: JobHandler
    concurrency: int = 1


async def noop(context: JobContext) -> dict[str, Any]:
    """Deterministic test job that reports progress and honours cancellation."""
    steps = int(context.payload.get("steps", 4))
    steps = max(1, min(steps, 50))
    delay = float(context.payload.get("delay", 0.02))
    delay = max(0.0, min(delay, 1.0))

    for step in range(1, steps + 1):
        await asyncio.sleep(delay)
        if await context.is_cancelled():
            raise JobCancelled
        await context.report_progress(step / steps)
    return {"steps": steps}


REGISTRY: dict[str, JobSpec] = {
    "noop": JobSpec(handler=noop, concurrency=2),
}


def get_spec(job_type: str) -> JobSpec | None:
    """Return the handler spec for a job type, if registered."""
    return REGISTRY.get(job_type)
