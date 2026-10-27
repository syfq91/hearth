"""Server-sent events for job progress, library updates, and now-playing state.

The stream polls the shared jobs table so worker updates are visible across
processes, and consumes in-memory broadcasts for immediate playback events.
Clients can authenticate with an ``Authorization: Bearer <token>`` header or
via ``?token=<access_token>`` query parameter (required for standard browser
``EventSource``).
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.jobs import JobResponse
from app.core.deps import EventsUser, SessionmakerDep
from app.db.base import utcnow
from app.db.models import Job, User
from app.services.events import get_broadcaster

router = APIRouter(tags=["events"])

DisconnectCheck = Callable[[], Awaitable[bool]]


def format_job_event(job: Job) -> str:
    """Serialize one job update as an SSE frame."""
    data = JobResponse.from_row(job).model_dump(mode="json")
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    return f"id: {job.id}\nevent: job\ndata: {payload}\n\n"


def format_scan_events(job: Job) -> list[str]:
    """Serialize scan progress, completion or failure events."""
    events: list[str] = []
    lib_id = job.payload.get("library_id") if isinstance(job.payload, dict) else None

    if job.status == "running":
        payload = json.dumps(
            {"job_id": job.id, "library_id": lib_id, "progress": job.progress},
            separators=(",", ":"),
            sort_keys=True,
        )
        events.append(f"id: {job.id}:progress\nevent: scan.progress\ndata: {payload}\n\n")
    elif job.status == "succeeded":
        res_payload = json.dumps(
            {"job_id": job.id, "library_id": lib_id, "result": job.result},
            separators=(",", ":"),
            sort_keys=True,
        )
        events.append(f"id: {job.id}:completed\nevent: scan.completed\ndata: {res_payload}\n\n")
        lib_payload = json.dumps(
            {"library_id": lib_id, "job_id": job.id},
            separators=(",", ":"),
            sort_keys=True,
        )
        events.append(f"id: {job.id}:lib_updated\nevent: library.updated\ndata: {lib_payload}\n\n")
    elif job.status == "failed":
        err_payload = json.dumps(
            {"job_id": job.id, "library_id": lib_id, "error": job.error},
            separators=(",", ":"),
            sort_keys=True,
        )
        events.append(f"id: {job.id}:failed\nevent: scan.failed\ndata: {err_payload}\n\n")

    return events


async def job_event_stream(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    user: User | None = None,
    poll_interval: float = 1.0,
    heartbeat_interval: float = 15.0,
    is_disconnected: DisconnectCheck | None = None,
) -> AsyncIterator[str]:
    """Yield initial active jobs, updates, broadcast events and heartbeats."""
    broadcaster = get_broadcaster()
    queue = await broadcaster.subscribe()

    async with sessionmaker() as session:
        active = (
            await session.scalars(
                select(Job).where(Job.status.in_(["pending", "running"])).order_by(Job.updated_at)
            )
        ).all()
        for job in active:
            yield format_job_event(job)
            if job.type == "scan.library":
                for ev in format_scan_events(job):
                    yield ev

    since = utcnow() - timedelta(seconds=1)
    last_heartbeat = time.monotonic()
    last_poll = 0.0

    try:
        while True:
            now = time.monotonic()

            # 1. Heartbeat
            if now - last_heartbeat >= heartbeat_interval:
                yield ": keepalive\n\n"
                last_heartbeat = now

            # 2. Drain broadcast queue (e.g. playback.active, playback.stopped)
            try:
                while True:
                    msg = queue.get_nowait()
                    event_type = msg["event"]
                    data = msg["data"]
                    if user and user.role != "admin":
                        target_user = data.get("user_id")
                        if target_user and target_user != user.id:
                            continue
                    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
                    yield f"event: {event_type}\ndata: {payload}\n\n"
            except asyncio.QueueEmpty:
                pass

            # 3. Poll DB jobs
            if now - last_poll >= poll_interval:
                last_poll = now
                async with sessionmaker() as session:
                    jobs = (
                        await session.scalars(
                            select(Job)
                            .where(Job.updated_at > since)
                            .order_by(Job.updated_at)
                            .limit(100)
                        )
                    ).all()

                for job in jobs:
                    yield format_job_event(job)
                    if job.type == "scan.library":
                        for ev in format_scan_events(job):
                            yield ev
                    if job.updated_at > since:
                        since = job.updated_at

            await asyncio.sleep(min(0.1, poll_interval))
    finally:
        await broadcaster.unsubscribe(queue)


@router.get("/events")
async def events(
    request: Request,
    user: EventsUser,
    sessionmaker: SessionmakerDep,
    token: Annotated[
        str | None,
        Query(description="Bearer access token for EventSource authentication"),
    ] = None,
) -> StreamingResponse:
    """Stream real-time job progress, scan events, and playback status."""

    async def generate() -> AsyncIterator[str]:
        yield ": connected\n\n"
        async for chunk in job_event_stream(
            sessionmaker,
            user=user,
            is_disconnected=request.is_disconnected,
        ):
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
