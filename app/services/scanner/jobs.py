"""Scan job handler and the helper used to enqueue targeted scans."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job
from app.services.jobs.types import JobCancelled, JobContext
from app.services.metadata.factory import build_metadata_matcher
from app.services.metadata.matching import MetadataMatcher
from app.services.scanner.scanner import LibraryScanner, ScanCancelled

logger = logging.getLogger("hearth.scanner.jobs")

SCAN_JOB_TYPE = "scan.library"


def build_scanner(context: JobContext, matcher: MetadataMatcher) -> LibraryScanner:
    """Build the scanner for a job; overridable in tests."""
    return LibraryScanner(context.settings, context.sessionmaker, matcher=matcher)


async def enqueue_scan(
    session: AsyncSession,
    library_id: str,
    *,
    targets: list[str] | None = None,
) -> tuple[Job, bool]:
    """Queue one scan per library, returning ``(job, created)``.

    If a scan for the library is already pending or running, its job is returned
    with ``created=False``. Callers own the transaction so a manual scan can be
    audited atomically with the job row.
    """
    active = await session.scalars(
        select(Job).where(
            Job.type == SCAN_JOB_TYPE,
            Job.status.in_(("pending", "running")),
        )
    )
    for existing in active:
        if (existing.payload or {}).get("library_id") == library_id:
            return existing, False

    payload: dict[str, Any] = {"library_id": library_id}
    if targets:
        payload["targets"] = sorted({str(target) for target in targets})
    job = Job(type=SCAN_JOB_TYPE, payload=payload)
    session.add(job)
    await session.flush()
    return job, True


async def scan_library_job(context: JobContext) -> dict[str, Any]:
    """Scan one library, optionally limited to watcher-reported targets."""
    library_id = context.payload.get("library_id")
    if not isinstance(library_id, str) or not library_id:
        raise ValueError("scan.library requires a string library_id")

    raw_targets = context.payload.get("targets")
    targets: list[str] | None = None
    if raw_targets is not None:
        if not isinstance(raw_targets, list) or not all(
            isinstance(target, str) for target in raw_targets
        ):
            raise ValueError("scan.library targets must be a list of paths")
        targets = [str(target) for target in raw_targets]

    matcher = build_metadata_matcher(context.settings, context.sessionmaker)
    scanner = build_scanner(context, matcher)
    try:
        stats = await scanner.scan_library(
            library_id,
            targets=targets,
            progress=context.report_progress,
            is_cancelled=context.is_cancelled,
        )
    except ScanCancelled as exc:
        raise JobCancelled from exc
    finally:
        await matcher.close()

    logger.info("scan job completed", extra=stats.to_dict())
    return stats.to_dict()
