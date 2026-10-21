"""Tests for the job API, worker lifecycle, retries and SSE events."""

import asyncio
import json
from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.events import job_event_stream
from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import Job
from app.services.jobs.handlers import JobContext, JobSpec
from app.services.jobs.worker import JobWorker

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _add_job(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    job_type: str = "noop",
    payload: dict[str, Any] | None = None,
    **values: Any,
) -> str:
    async with sessionmaker() as session:
        job = Job(type=job_type, payload=payload or {}, **values)
        session.add(job)
        await session.commit()
        return job.id


async def _get_job(sessionmaker: async_sessionmaker[AsyncSession], job_id: str) -> Job:
    async with sessionmaker() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        return job


async def test_job_api_requires_admin(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get("/api/v1/jobs")).status_code == 401
    assert (await api_client.post("/api/v1/jobs", json={"type": "noop"})).status_code == 401


async def test_enqueue_and_cancel_pending_job(api_client: httpx.AsyncClient) -> None:
    admin = await _setup(api_client)

    created = await api_client.post(
        "/api/v1/jobs",
        json={"type": "noop", "payload": {"steps": 2}},
        headers=_bearer(admin),
    )
    assert created.status_code == 201, created.text
    job = created.json()
    assert job["status"] == "pending"
    assert job["attempts"] == 0
    assert job["max_attempts"] == 3

    cancelled = await api_client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=_bearer(admin))
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancel_requested"] is True

    again = await api_client.post(f"/api/v1/jobs/{job['id']}/cancel", headers=_bearer(admin))
    assert again.status_code == 409

    listed = await api_client.get(
        "/api/v1/jobs", params={"status": "cancelled"}, headers=_bearer(admin)
    )
    assert [item["id"] for item in listed.json()] == [job["id"]]


async def test_unknown_job_type_is_rejected(api_client: httpx.AsyncClient) -> None:
    admin = await _setup(api_client)

    response = await api_client.post(
        "/api/v1/jobs", json={"type": "not-a-job"}, headers=_bearer(admin)
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_job_type"


async def test_worker_runs_noop_job_to_completion(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _add_job(
        db_sessionmaker,
        payload={"steps": 3, "delay": 0},
    )
    worker = JobWorker(db_settings, db_sessionmaker, worker_id="test-worker")

    await worker.drain()

    job = await _get_job(db_sessionmaker, job_id)
    assert job.status == "succeeded"
    assert job.progress == 1.0
    assert job.attempts == 1
    assert job.result == {"steps": 3}
    assert job.finished_at is not None


async def test_running_job_is_requeued_after_restart(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _add_job(
        db_sessionmaker,
        payload={"steps": 1, "delay": 0},
        status="running",
        attempts=1,
        claimed_by="dead-worker",
        started_at=utcnow(),
    )
    worker = JobWorker(db_settings, db_sessionmaker, worker_id="new-worker")

    requeued = await worker.requeue_running()
    assert requeued == 1
    assert (await _get_job(db_sessionmaker, job_id)).status == "pending"

    await worker.drain()
    job = await _get_job(db_sessionmaker, job_id)
    assert job.status == "succeeded"
    assert job.attempts == 2


async def test_failed_job_exhausts_attempts_and_records_error(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    async def boom(_context: JobContext) -> None:
        raise RuntimeError("synthetic failure")

    registry = {"boom": JobSpec(handler=boom, concurrency=1)}
    db_settings.job_retry_base_seconds = 0
    job_id = await _add_job(db_sessionmaker, job_type="boom", max_attempts=2)
    worker = JobWorker(db_settings, db_sessionmaker, registry=registry, worker_id="t")

    await worker.drain()
    first = await _get_job(db_sessionmaker, job_id)
    assert first.status == "failed"
    assert first.attempts == 2
    assert "synthetic failure" in (first.error or "")


async def test_failed_job_waits_for_backoff_before_retry(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    db_settings.job_retry_base_seconds = 60
    calls = 0

    async def flaky(_context: JobContext) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("transient")

    registry = {"flaky": JobSpec(handler=flaky, concurrency=1)}
    job_id = await _add_job(db_sessionmaker, job_type="flaky", max_attempts=2)
    worker = JobWorker(db_settings, db_sessionmaker, registry=registry, worker_id="t")

    await worker.drain()
    job = await _get_job(db_sessionmaker, job_id)
    assert job.status == "pending"
    assert job.attempts == 1
    assert job.run_after > utcnow()
    assert calls == 1


async def test_cancellation_stops_a_running_handler(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    job_id = await _add_job(db_sessionmaker, payload={"steps": 50, "delay": 0.02})
    worker = JobWorker(db_settings, db_sessionmaker, worker_id="cancel-worker")
    task = asyncio.create_task(worker.process_one())

    for _ in range(100):
        job = await _get_job(db_sessionmaker, job_id)
        if job.status == "running":
            break
        await asyncio.sleep(0.01)
    assert job.status == "running"

    async with db_sessionmaker() as session:
        await session.execute(update(Job).where(Job.id == job_id).values(cancel_requested=True))
        await session.commit()
    await asyncio.wait_for(task, timeout=5)

    job = await _get_job(db_sessionmaker, job_id)
    assert job.status == "cancelled"


async def test_event_stream_emits_snapshot_and_progress(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    job_id = await _add_job(db_sessionmaker, payload={"steps": 1, "delay": 0})
    stream = job_event_stream(db_sessionmaker, poll_interval=0.01, heartbeat_interval=100)
    try:
        first = await asyncio.wait_for(anext(stream), timeout=2)
        assert "event: job" in first
        data = json.loads(first.split("data: ", 1)[1])
        assert data["id"] == job_id
        assert data["status"] == "pending"

        async with db_sessionmaker() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            job.progress = 0.5
            await session.commit()

        seen_progress = False
        for _ in range(10):
            frame = await asyncio.wait_for(anext(stream), timeout=2)
            if "event: job" not in frame:
                continue
            payload = json.loads(frame.split("data: ", 1)[1])
            if payload["id"] == job_id and payload["progress"] == 0.5:
                seen_progress = True
                break
        assert seen_progress
    finally:
        await stream.aclose()


async def test_event_stream_requires_admin(api_client: httpx.AsyncClient) -> None:
    assert (await api_client.get("/api/v1/events")).status_code == 401
