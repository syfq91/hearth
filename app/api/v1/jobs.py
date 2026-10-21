"""Job submission, inspection and cancellation endpoints."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import RequireAdmin
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.base import utcnow
from app.db.models import Job
from app.db.session import SessionDep
from app.services.audit import record_audit
from app.services.jobs.handlers import REGISTRY

router = APIRouter(prefix="/jobs", tags=["jobs"])

JobStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]


class JobCreate(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=10)


class JobResponse(BaseModel):
    id: str
    type: str
    payload: dict[str, Any]
    status: str
    progress: float
    error: str | None
    result: dict[str, Any] | None
    attempts: int
    max_attempts: int
    run_after: datetime
    claimed_by: str | None
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_row(cls, job: Job) -> "JobResponse":
        return cls(
            id=job.id,
            type=job.type,
            payload=job.payload,
            status=job.status,
            progress=job.progress,
            error=job.error,
            result=job.result,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            run_after=job.run_after,
            claimed_by=job.claimed_by,
            cancel_requested=job.cancel_requested,
            created_at=job.created_at,
            updated_at=job.updated_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )


async def _load_job(session: SessionDep, job_id: str) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise NotFoundError("Job not found", code="job_not_found")
    return job


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreate,
    actor: RequireAdmin,
    session: SessionDep,
) -> JobResponse:
    """Enqueue a job of a registered type."""
    if payload.type not in REGISTRY:
        raise ValidationError(f"Unknown job type: {payload.type}", code="unknown_job_type")

    job = Job(
        type=payload.type,
        payload=payload.payload,
        max_attempts=payload.max_attempts,
        run_after=utcnow(),
    )
    session.add(job)
    await session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="job.create",
        target_type="job",
        target_id=job.id,
        details={"type": job.type},
    )
    await session.commit()
    return JobResponse.from_row(job)


@router.get("")
async def list_jobs(
    _actor: RequireAdmin,
    session: SessionDep,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    type_filter: Annotated[str | None, Query(alias="type", max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JobResponse]:
    """List jobs newest first with optional filters."""
    query = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status_filter is not None:
        query = query.where(Job.status == status_filter)
    if type_filter is not None:
        query = query.where(Job.type == type_filter)
    jobs = (await session.scalars(query)).all()
    return [JobResponse.from_row(job) for job in jobs]


@router.get("/{job_id}")
async def get_job(job_id: str, _actor: RequireAdmin, session: SessionDep) -> JobResponse:
    """Return one job."""
    return JobResponse.from_row(await _load_job(session, job_id))


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, actor: RequireAdmin, session: SessionDep) -> JobResponse:
    """Cancel a pending job or request cancellation of a running one."""
    job = await _load_job(session, job_id)
    if job.status in {"succeeded", "failed", "cancelled"}:
        raise ConflictError(f"Job is already {job.status}", code="job_not_cancellable")

    job.cancel_requested = True
    if job.status == "pending":
        job.status = "cancelled"
        job.finished_at = utcnow()
    record_audit(
        session,
        actor_id=actor.id,
        action="job.cancel",
        target_type="job",
        target_id=job.id,
        details={"type": job.type},
    )
    await session.commit()
    return JobResponse.from_row(job)
