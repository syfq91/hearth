"""Job handler registry and the built-in handlers."""

import asyncio
from typing import Any

from app.services.jobs.types import JobCancelled, JobContext, JobHandler, JobSpec
from app.services.scanner.jobs import SCAN_JOB_TYPE, scan_library_job

__all__ = [
    "REGISTRY",
    "SCAN_JOB_TYPE",
    "JobCancelled",
    "JobContext",
    "JobHandler",
    "JobSpec",
    "get_spec",
    "noop",
]


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
    SCAN_JOB_TYPE: JobSpec(handler=scan_library_job, concurrency=1),
}


def get_spec(job_type: str) -> JobSpec | None:
    """Return the handler spec for a job type, if registered."""
    return REGISTRY.get(job_type)
