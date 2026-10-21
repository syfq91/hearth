"""Durable asyncio job worker with claiming, retries, cancellation and requeue."""

import asyncio
import contextlib
import logging
import os
import socket
from datetime import timedelta
from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.ids import uuid7
from app.db.base import utcnow
from app.db.models import Job
from app.services.jobs.handlers import (
    REGISTRY,
    JobCancelled,
    JobContext,
    JobSpec,
)

logger = logging.getLogger("hearth.jobs.worker")


class JobWorker:
    """Claim and execute jobs from the shared ``jobs`` table.

    Claiming uses a single conditional UPDATE with RETURNING, so an API process
    and a worker (or two workers during a restart) cannot execute the same row
    twice. Running jobs left by a dead process are requeued at startup.
    """

    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        registry: dict[str, JobSpec] | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.registry = registry if registry is not None else REGISTRY
        self.worker_id = worker_id or (f"{socket.gethostname()}:{os.getpid()}:{uuid7()[:8]}")

    async def requeue_running(self) -> int:
        """Return jobs left running by a previous process to the queue."""
        now = utcnow()
        async with self.sessionmaker() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(Job)
                    .where(Job.status == "running")
                    .values(
                        status="pending",
                        claimed_by=None,
                        started_at=None,
                        run_after=now,
                        updated_at=now,
                    )
                ),
            )
            await session.commit()
        count = result.rowcount or 0
        if count:
            logger.warning("requeued interrupted jobs", extra={"count": count})
        return count

    async def _running_counts(self) -> dict[str, int]:
        async with self.sessionmaker() as session:
            rows = await session.execute(
                select(Job.type, func.count()).where(Job.status == "running").group_by(Job.type)
            )
            return {job_type: int(count) for job_type, count in rows.all()}

    def _available_types(self, running: dict[str, int]) -> list[str]:
        total_running = sum(running.values())
        if total_running >= self.settings.job_worker_concurrency:
            return []
        return [
            job_type
            for job_type, spec in self.registry.items()
            if running.get(job_type, 0) < spec.concurrency
        ]

    async def claim(self) -> Job | None:
        """Atomically claim one due job within the configured concurrency."""
        running = await self._running_counts()
        available = self._available_types(running)
        if not available:
            return None

        now = utcnow()
        candidate = (
            select(Job.id)
            .where(
                Job.status == "pending",
                Job.run_after <= now,
                Job.type.in_(available),
            )
            .order_by(Job.run_after, Job.created_at)
            .limit(1)
            .scalar_subquery()
        )
        async with self.sessionmaker() as session:
            result = await session.execute(
                update(Job)
                .where(Job.id == candidate, Job.status == "pending")
                .values(
                    status="running",
                    claimed_by=self.worker_id,
                    started_at=now,
                    attempts=Job.attempts + 1,
                    updated_at=now,
                )
                .returning(Job.id)
            )
            job_id = result.scalar_one_or_none()
            await session.commit()
            if job_id is None:
                return None
            return await session.get(Job, job_id)

    async def _report_progress(self, job_id: str, value: float) -> None:
        clamped = max(0.0, min(1.0, value))
        async with self.sessionmaker() as session:
            await session.execute(
                update(Job).where(Job.id == job_id).values(progress=clamped, updated_at=utcnow())
            )
            await session.commit()

    async def _is_cancelled(self, job_id: str) -> bool:
        async with self.sessionmaker() as session:
            return bool(await session.scalar(select(Job.cancel_requested).where(Job.id == job_id)))

    async def _finish(
        self,
        job_id: str,
        *,
        status: str,
        error: str | None = None,
        result: dict[str, Any] | None = None,
        progress: float | None = None,
    ) -> None:
        now = utcnow()
        values: dict[str, Any] = {
            "status": status,
            "error": error,
            "updated_at": now,
            "finished_at": now if status in {"succeeded", "failed", "cancelled"} else None,
        }
        if result is not None:
            values["result"] = result
        if progress is not None:
            values["progress"] = progress
        async with self.sessionmaker() as session:
            await session.execute(update(Job).where(Job.id == job_id).values(**values))
            await session.commit()

    async def _retry_or_fail(
        self, job_id: str, attempts: int, max_attempts: int, error: str
    ) -> None:
        if attempts < max_attempts:
            delay = min(
                self.settings.job_retry_max_seconds,
                self.settings.job_retry_base_seconds * (2 ** max(0, attempts - 1)),
            )
            now = utcnow()
            async with self.sessionmaker() as session:
                await session.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        status="pending",
                        error=error[:2000],
                        run_after=now + timedelta(seconds=delay),
                        claimed_by=None,
                        started_at=None,
                        updated_at=now,
                    )
                )
                await session.commit()
            logger.warning(
                "job failed; retrying",
                extra={"job_id": job_id, "attempts": attempts, "delay": delay},
            )
            return
        await self._finish(job_id, status="failed", error=error[:2000])
        logger.error("job failed permanently", extra={"job_id": job_id, "attempts": attempts})

    async def execute(self, job: Job) -> None:
        """Run one already-claimed job to a terminal or retry state."""
        spec = self.registry.get(job.type)
        if spec is None:
            await self._finish(job.id, status="failed", error=f"Unknown job type: {job.type}")
            return

        context = JobContext(
            job_id=job.id,
            payload=job.payload or {},
            report_progress=lambda value: self._report_progress(job.id, value),
            is_cancelled=lambda: self._is_cancelled(job.id),
            settings=self.settings,
            sessionmaker=self.sessionmaker,
        )
        try:
            result = await spec.handler(context)
        except JobCancelled:
            await self._finish(job.id, status="cancelled")
            logger.info("job cancelled", extra={"job_id": job.id})
        except asyncio.CancelledError:
            # Leave the row running; shutdown requeues it so another process
            # can resume without losing the attempt count.
            raise
        except Exception as exc:  # noqa: BLE001 - handler failures are data
            logger.exception("job handler raised", extra={"job_id": job.id})
            await self._retry_or_fail(job.id, job.attempts, job.max_attempts, str(exc))
        else:
            await self._finish(job.id, status="succeeded", result=result, progress=1.0)
            logger.info("job succeeded", extra={"job_id": job.id})

    async def process_one(self) -> bool:
        """Claim and execute at most one job. Returns False when idle."""
        job = await self.claim()
        if job is None:
            return False
        await self.execute(job)
        return True

    async def drain(self) -> None:
        """Process every currently due job sequentially (tests and CLI drain)."""
        await self.requeue_running()
        while await self.process_one():
            pass

    async def run(self, stop: asyncio.Event) -> None:
        """Poll and execute jobs until ``stop`` is set, then requeue in-flight work."""
        await self.requeue_running()
        logger.info("job worker started", extra={"worker_id": self.worker_id})
        tasks: set[asyncio.Task[None]] = set()
        try:
            while not stop.is_set():
                tasks = {task for task in tasks if not task.done()}
                if len(tasks) < self.settings.job_worker_concurrency:
                    job = await self.claim()
                    if job is not None:
                        tasks.add(asyncio.create_task(self.execute(job)))
                        continue
                wait_for = self.settings.job_poll_interval if not tasks else 0.05
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=wait_for)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self._requeue_own_running()
            logger.info("job worker stopped", extra={"worker_id": self.worker_id})

    async def _requeue_own_running(self) -> None:
        now = utcnow()
        async with self.sessionmaker() as session:
            await session.execute(
                update(Job)
                .where(Job.status == "running", Job.claimed_by == self.worker_id)
                .values(
                    status="pending",
                    claimed_by=None,
                    started_at=None,
                    run_after=now,
                    updated_at=now,
                )
            )
            await session.commit()
