"""Server-sent events for job progress.

The stream polls the shared jobs table rather than using an in-memory queue so
events are visible whether the job runs in the API process or the separate
worker process. Clients should connect with ``fetch`` so they can send the
bearer header; ``EventSource`` cannot attach one.
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.jobs import JobResponse
from app.core.deps import RequireAdmin, SessionmakerDep
from app.db.base import utcnow
from app.db.models import Job

router = APIRouter(tags=["events"])

DisconnectCheck = Callable[[], Awaitable[bool]]


def format_job_event(job: Job) -> str:
    """Serialize one job update as an SSE frame."""
    data = JobResponse.from_row(job).model_dump(mode="json")
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    return f"id: {job.id}\nevent: job\ndata: {payload}\n\n"


async def job_event_stream(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    poll_interval: float = 1.0,
    heartbeat_interval: float = 15.0,
    is_disconnected: DisconnectCheck | None = None,
) -> AsyncIterator[str]:
    """Yield an initial active-job snapshot, then updates and heartbeats."""
    async with sessionmaker() as session:
        active = (
            await session.scalars(
                select(Job).where(Job.status.in_(["pending", "running"])).order_by(Job.updated_at)
            )
        ).all()
        for job in active:
            yield format_job_event(job)

    # One second of overlap tolerates clock granularity; duplicate updates are
    # harmless for clients that key on job id.
    since = utcnow() - timedelta(seconds=1)
    last_heartbeat = time.monotonic()

    while True:
        if is_disconnected is not None and await is_disconnected():
            return

        async with sessionmaker() as session:
            jobs = (
                await session.scalars(
                    select(Job).where(Job.updated_at > since).order_by(Job.updated_at).limit(100)
                )
            ).all()

        for job in jobs:
            yield format_job_event(job)
            if job.updated_at > since:
                since = job.updated_at

        now = time.monotonic()
        if now - last_heartbeat >= heartbeat_interval:
            yield ": keepalive\n\n"
            last_heartbeat = now

        await asyncio.sleep(poll_interval)


@router.get("/events")
async def events(
    request: Request, _actor: RequireAdmin, sessionmaker: SessionmakerDep
) -> StreamingResponse:
    """Stream job progress to an authenticated administrator."""

    async def generate() -> AsyncIterator[str]:
        async for chunk in job_event_stream(sessionmaker, is_disconnected=request.is_disconnected):
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
