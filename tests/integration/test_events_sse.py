"""Integration tests for Server-Sent Events (SSE) stream, token auth, and real-time events."""

import asyncio
import json

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.events import job_event_stream
from app.core.config import Settings
from app.core.deps import get_events_user
from app.core.errors import UnauthorizedError
from app.core.secrets import load_keys
from app.db.base import utcnow
from app.db.models import Job, User
from app.services.events import get_broadcaster

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    res = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert res.status_code == 201
    return res.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
async def test_events_token_auth_dependency(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin_tokens = await _setup(api_client)
    token = admin_tokens["access_token"]
    keys = load_keys(db_settings)

    # 1. Missing credentials and token -> 401
    async with db_sessionmaker() as session:
        with pytest.raises(UnauthorizedError):
            await get_events_user(
                credentials=None,
                settings=db_settings,
                keys=keys,
                session=session,
                token=None,
            )

    # 2. Query param ?token=<jwt> -> Validated user
    async with db_sessionmaker() as session:
        user = await get_events_user(
            credentials=None,
            settings=db_settings,
            keys=keys,
            session=session,
            token=token,
        )
        assert user.username == "admin"
        assert user.role == "admin"

    # 3. HTTP endpoint rejects unauthenticated request
    unauth = await api_client.get("/api/v1/events")
    assert unauth.status_code == 401

    # 4. HTTP endpoint rejects invalid query token
    bad = await api_client.get("/api/v1/events?token=invalid.jwt.token")
    assert bad.status_code == 401


@pytest.mark.asyncio
async def test_job_event_stream_scan_progress_and_completion(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessionmaker() as session:
        job = Job(
            type="scan.library",
            payload={"library_id": "lib_test_123"},
            status="pending",
            run_after=utcnow(),
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    stream = job_event_stream(db_sessionmaker, poll_interval=0.01, heartbeat_interval=100)
    try:
        # First event is initial pending job snapshot
        first = await asyncio.wait_for(anext(stream), timeout=2.0)
        assert "event: job" in first
        data = json.loads(first.split("data: ", 1)[1])
        assert data["id"] == job_id

        # Update job to running (progress=0.5)
        async with db_sessionmaker() as session:
            j = await session.get(Job, job_id)
            assert j is not None
            j.status = "running"
            j.progress = 0.5
            j.updated_at = utcnow()
            await session.commit()

        # Stream should emit scan.progress
        seen_progress = False
        for _ in range(10):
            frame = await asyncio.wait_for(anext(stream), timeout=2.0)
            if "event: scan.progress" in frame:
                seen_progress = True
                p_data = json.loads(frame.split("data: ", 1)[1])
                assert p_data["job_id"] == job_id
                assert p_data["progress"] == 0.5
                assert p_data["library_id"] == "lib_test_123"
                break
        assert seen_progress

        # Update job to succeeded
        async with db_sessionmaker() as session:
            j = await session.get(Job, job_id)
            assert j is not None
            j.status = "succeeded"
            j.progress = 1.0
            j.result = {"added": 42}
            j.updated_at = utcnow()
            await session.commit()

        seen_completed = False
        seen_lib_updated = False
        for _ in range(10):
            frame = await asyncio.wait_for(anext(stream), timeout=2.0)
            if "event: scan.completed" in frame:
                seen_completed = True
            if "event: library.updated" in frame:
                seen_lib_updated = True
            if seen_completed and seen_lib_updated:
                break
        assert seen_completed
        assert seen_lib_updated
    finally:
        await stream.aclose()


@pytest.mark.asyncio
async def test_job_event_stream_now_playing_broadcast(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    broadcaster = get_broadcaster()

    fake_admin = User(id="user_admin", username="admin", role="admin")
    stream = job_event_stream(
        db_sessionmaker,
        user=fake_admin,
        poll_interval=0.01,
        heartbeat_interval=100,
    )
    try:
        # Start the stream in background task to initialize subscription and enter loop
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.02)

        # Publish playback.active event
        broadcaster.publish(
            "playback.active",
            {
                "user_id": "user_admin",
                "item_id": "item_456",
                "position_ms": 12000,
                "title": "Cosmic Voyage",
                "type": "movie",
            },
        )

        frame = await asyncio.wait_for(task, timeout=2.0)
        assert "event: playback.active" in frame
        payload = json.loads(frame.split("data: ", 1)[1])
        assert payload["title"] == "Cosmic Voyage"
        assert payload["item_id"] == "item_456"

        # Next frame: publish playback.stopped
        task_stop = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.02)

        broadcaster.publish(
            "playback.stopped",
            {"user_id": "user_admin", "item_id": "item_456", "position_ms": 25000},
        )

        frame_stop = await asyncio.wait_for(task_stop, timeout=2.0)
        assert "event: playback.stopped" in frame_stop
        payload_stop = json.loads(frame_stop.split("data: ", 1)[1])
        assert payload_stop["item_id"] == "item_456"
    finally:
        await stream.aclose()
