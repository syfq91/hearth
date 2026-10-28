"""Administrator controls, system diagnostics, and audit logs."""

import asyncio
import os
import platform
import resource
import shutil
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.api.v1.jobs import JobResponse, cancel_job
from app.core.deps import RequireAdmin, SettingsDep
from app.core.errors import NotFoundError
from app.core.logging import get_recent_logs
from app.db.base import utcnow
from app.db.models import AuditLog, Job, Library, MediaItem, User
from app.db.session import SessionDep
from app.services.audit import record_audit
from app.services.backup import create_backup, restore_backup
from app.services.media.titles import sort_title
from app.services.scanner.jobs import enqueue_scan
from app.services.transcode.capabilities import (
    ServerTranscodeCapabilities,
    probe_transcode_capabilities,
)
from app.services.transcode.session import get_session_manager

router = APIRouter(prefix="/admin", tags=["admin"])


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]
    created_at: datetime

    @classmethod
    def from_row(cls, row: AuditLog) -> "AuditEntry":
        return cls(
            id=row.id,
            actor_id=row.actor_id,
            action=row.action,
            target_type=row.target_type,
            target_id=row.target_id,
            details=row.details,
            created_at=row.created_at,
        )


class TranscodeSessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    user_id: str
    item_id: str
    file_id: str
    mode: str
    is_hardware: bool
    hwaccel: str
    fps: float | None = None
    speed: str | None = None
    frame: int | None = None
    uptime_seconds: float
    created_at: datetime
    last_activity_at: datetime
    cmd: list[str] = Field(default_factory=list)


class TranscodeLogsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    lines: list[str]


class UnmatchedItemResponse(BaseModel):
    """An item needing provider matching or with a match error."""

    model_config = ConfigDict(extra="forbid")

    id: str
    library_id: str
    type: str
    title: str
    year: int | None = None
    match_state: str
    match_attempts: int
    match_error: str | None = None
    added_at: datetime


class RematchRequest(BaseModel):
    """Force rematching with optional manual metadata hints."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    year: int | None = None
    provider_id: str | None = None


class DiskUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_bytes: int
    used_bytes: int
    free_bytes: int


class AdminSystemInfoResponse(BaseModel):
    """Host environment, resource metrics, and storage diagnostics."""

    model_config = ConfigDict(extra="forbid")

    os_platform: str
    os_release: str
    python_version: str
    sqlite_version: str
    pid: int
    memory_rss_bytes: int
    cpu_count: int
    media_disk: DiskUsage
    data_disk: DiskUsage
    transcode_disk: DiskUsage
    transcode_capabilities: ServerTranscodeCapabilities


class LogEntryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timestamp: str
    level: str
    logger: str
    message: str
    request_id: str | None = None
    exception: str | None = None


class TranscodeSettingsResponse(BaseModel):
    """Active runtime transcode parameters."""

    model_config = ConfigDict(extra="forbid")

    force_software: bool
    max_hw_sessions: int
    max_sw_sessions: int
    idle_timeout_seconds: float
    segment_duration_seconds: int
    audio_bitrate_kbps: int
    default_max_bitrate: int | None = None


class UpdateTranscodeSettingsRequest(BaseModel):
    """Update runtime transcode settings."""

    model_config = ConfigDict(extra="forbid")

    force_software: bool | None = None
    max_hw_sessions: int | None = Field(default=None, ge=1, le=32)
    max_sw_sessions: int | None = Field(default=None, ge=1, le=32)
    idle_timeout_seconds: float | None = Field(default=None, ge=10.0, le=600.0)
    audio_bitrate_kbps: int | None = Field(default=None, ge=64, le=512)
    default_max_bitrate: int | None = Field(default=None, ge=500_000)


class AdminRestoreResponse(BaseModel):
    """Response returned after restoring a database snapshot."""

    model_config = ConfigDict(extra="forbid")

    status: str
    schema_version: str
    created_at: str
    hearth_version: str


class TranscodeStopResponse(BaseModel):
    """Response returned after terminating an active transcode session."""

    model_config = ConfigDict(extra="forbid")

    status: str
    session_id: str


@router.get("/audit", response_model=list[AuditEntry])
async def list_audit(
    _actor: RequireAdmin,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    action: str | None = Query(default=None, max_length=64),
) -> list[AuditEntry]:
    """Return the most recent administrative actions, newest first."""
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if action is not None:
        query = query.where(AuditLog.action == action)
    rows = (await session.scalars(query)).all()
    return [AuditEntry.from_row(row) for row in rows]


# ------------------------------------------------------------- library scan controls


@router.post(
    "/libraries/{library_id}/scan",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def scan_single_library(
    library_id: str,
    actor: RequireAdmin,
    session: SessionDep,
) -> JobResponse:
    """Trigger an immediate scan of a specific library."""
    library = await session.get(Library, library_id)
    if library is None:
        raise NotFoundError("Library not found", code="library_not_found")

    job, created = await enqueue_scan(session, library.id)
    if created:
        record_audit(
            session,
            actor_id=actor.id,
            action="library.scan",
            target_type="library",
            target_id=library.id,
            details={"name": library.name, "job_id": job.id},
        )
    await session.commit()
    return JobResponse.from_row(job)


@router.post(
    "/libraries/scan",
    response_model=list[JobResponse],
    status_code=status.HTTP_202_ACCEPTED,
)
async def scan_all_libraries(
    actor: RequireAdmin,
    session: SessionDep,
) -> list[JobResponse]:
    """Trigger an immediate scan across all libraries."""
    libraries = list((await session.scalars(select(Library))).all())
    job_responses: list[JobResponse] = []

    for library in libraries:
        job, created = await enqueue_scan(session, library.id)
        if created:
            record_audit(
                session,
                actor_id=actor.id,
                action="library.scan",
                target_type="library",
                target_id=library.id,
                details={"name": library.name, "job_id": job.id},
            )
        job_responses.append(JobResponse.from_row(job))

    await session.commit()
    return job_responses


# ------------------------------------------------------------- unmatched queue & rematch


@router.get("/unmatched", response_model=list[UnmatchedItemResponse])
async def list_unmatched_items(
    _actor: RequireAdmin,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[UnmatchedItemResponse]:
    """List media items pending match or with match errors."""
    query = (
        select(MediaItem)
        .where(MediaItem.match_state.in_(("unmatched", "pending")))
        .order_by(MediaItem.added_at.desc())
        .limit(limit)
    )
    items = list((await session.scalars(query)).all())
    return [
        UnmatchedItemResponse(
            id=item.id,
            library_id=item.library_id,
            type=item.type,
            title=item.title,
            year=item.year,
            match_state=item.match_state,
            match_attempts=item.match_attempts,
            match_error=item.match_error,
            added_at=item.added_at,
        )
        for item in items
    ]


@router.post("/items/{item_id}/rematch", response_model=UnmatchedItemResponse)
async def rematch_item(
    item_id: str,
    body: RematchRequest,
    actor: RequireAdmin,
    session: SessionDep,
) -> UnmatchedItemResponse:
    """Reset item match state with optional manual hints to force immediate matching."""
    item = await session.get(MediaItem, item_id)
    if item is None:
        raise NotFoundError("Item not found", code="item_not_found")

    if body.title is not None:
        clean = body.title.strip()
        if clean:
            item.title = clean
            item.sort_title = sort_title(clean)
    if body.year is not None:
        item.year = body.year
    if body.provider_id is not None:
        clean_pid = body.provider_id.strip()
        provider_ids = dict(item.provider_ids or {})
        provider_ids["tmdb"] = clean_pid
        item.provider_ids = provider_ids

    now = utcnow()
    item.match_state = "pending"
    item.match_attempts = 0
    item.match_error = None
    item.next_match_at = now
    item.updated_at = now

    record_audit(
        session,
        actor_id=actor.id,
        action="item.rematch",
        target_type="item",
        target_id=item.id,
        details={"title": item.title, "hints": body.model_dump(exclude_none=True)},
    )
    await session.commit()

    return UnmatchedItemResponse(
        id=item.id,
        library_id=item.library_id,
        type=item.type,
        title=item.title,
        year=item.year,
        match_state=item.match_state,
        match_attempts=item.match_attempts,
        match_error=item.match_error,
        added_at=item.added_at,
    )


# ------------------------------------------------------------- jobs & diagnostics


@router.get("/jobs", response_model=list[JobResponse])
async def list_admin_jobs(
    _actor: RequireAdmin,
    session: SessionDep,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[JobResponse]:
    """List background jobs with status filter."""
    query = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if status_filter:
        query = query.where(Job.status == status_filter)
    rows = (await session.scalars(query)).all()
    return [JobResponse.from_row(job) for job in rows]


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_admin_job(
    job_id: str,
    actor: RequireAdmin,
    session: SessionDep,
) -> JobResponse:
    """Request cancellation of an active or pending background job."""
    return await cancel_job(job_id=job_id, actor=actor, session=session)


def _get_disk_usage(path: str | os.PathLike[str]) -> DiskUsage:
    try:
        usage = shutil.disk_usage(path)
        return DiskUsage(
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
        )
    except Exception:
        return DiskUsage(total_bytes=0, used_bytes=0, free_bytes=0)


@router.get("/system/info", response_model=AdminSystemInfoResponse)
async def get_admin_system_info(
    _actor: RequireAdmin,
    settings: SettingsDep,
) -> AdminSystemInfoResponse:
    """Comprehensive host environment, memory, disk, and transcoding telemetry."""
    # Process memory
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_bytes = int(rss_kb * 1024)

    caps = await probe_transcode_capabilities(settings)

    return AdminSystemInfoResponse(
        os_platform=platform.system(),
        os_release=platform.release(),
        python_version=platform.python_version(),
        sqlite_version=sqlite3.sqlite_version,
        pid=os.getpid(),
        memory_rss_bytes=rss_bytes,
        cpu_count=os.cpu_count() or 1,
        media_disk=_get_disk_usage(settings.media_dir),
        data_disk=_get_disk_usage(settings.data_dir),
        transcode_disk=_get_disk_usage(settings.transcode_dir),
        transcode_capabilities=caps,
    )


@router.get("/logs", response_model=list[LogEntryResponse])
async def list_admin_logs(
    _actor: RequireAdmin,
    limit: int = Query(default=100, ge=1, le=500),
    level: str | None = Query(default=None, max_length=16),
) -> list[LogEntryResponse]:
    """Tail recent in-memory log entries in reverse chronological order."""
    entries = get_recent_logs(limit=limit, level=level)
    return [LogEntryResponse.model_validate(e) for e in entries]


# ------------------------------------------------------------- transcode settings


@router.get("/transcoding/settings", response_model=TranscodeSettingsResponse)
async def get_transcode_settings(
    _actor: RequireAdmin,
    settings: SettingsDep,
) -> TranscodeSettingsResponse:
    """Return runtime transcoding parameters."""
    return TranscodeSettingsResponse(
        force_software=settings.transcode_force_software,
        max_hw_sessions=settings.transcode_max_hw_sessions,
        max_sw_sessions=settings.transcode_max_sw_sessions,
        idle_timeout_seconds=settings.transcode_idle_timeout_seconds,
        segment_duration_seconds=settings.transcode_segment_duration_seconds,
        audio_bitrate_kbps=settings.transcode_audio_bitrate_kbps,
        default_max_bitrate=getattr(settings, "default_max_bitrate", None),
    )


@router.put("/transcoding/settings", response_model=TranscodeSettingsResponse)
async def update_transcode_settings(
    body: UpdateTranscodeSettingsRequest,
    actor: RequireAdmin,
    request: Request,
    settings: SettingsDep,
    session: SessionDep,
) -> TranscodeSettingsResponse:
    """Update runtime transcoding parameters."""
    if body.force_software is not None:
        settings.transcode_force_software = body.force_software
    if body.max_hw_sessions is not None:
        settings.transcode_max_hw_sessions = body.max_hw_sessions
    if body.max_sw_sessions is not None:
        settings.transcode_max_sw_sessions = body.max_sw_sessions
    if body.idle_timeout_seconds is not None:
        settings.transcode_idle_timeout_seconds = body.idle_timeout_seconds
    if body.audio_bitrate_kbps is not None:
        settings.transcode_audio_bitrate_kbps = body.audio_bitrate_kbps
    if body.default_max_bitrate is not None:
        settings.default_max_bitrate = body.default_max_bitrate  # type: ignore[attr-defined]

    record_audit(
        session,
        actor_id=actor.id,
        action="transcoding.settings_updated",
        target_type="system",
        target_id="transcode",
        details=body.model_dump(exclude_none=True),
    )
    await session.commit()

    return TranscodeSettingsResponse(
        force_software=settings.transcode_force_software,
        max_hw_sessions=settings.transcode_max_hw_sessions,
        max_sw_sessions=settings.transcode_max_sw_sessions,
        idle_timeout_seconds=settings.transcode_idle_timeout_seconds,
        segment_duration_seconds=settings.transcode_segment_duration_seconds,
        audio_bitrate_kbps=settings.transcode_audio_bitrate_kbps,
        default_max_bitrate=getattr(settings, "default_max_bitrate", None),
    )


# ------------------------------------------------------------- transcode diagnostics


@router.get("/transcoding/capabilities")
async def get_transcoding_capabilities(
    _actor: RequireAdmin,
    settings: SettingsDep,
) -> ServerTranscodeCapabilities:
    """Return hardware and software transcoding capabilities."""
    return await probe_transcode_capabilities(settings)


@router.get("/transcoding/sessions", response_model=list[TranscodeSessionResponse])
async def list_transcoding_sessions(
    _actor: RequireAdmin,
    settings: SettingsDep,
) -> list[TranscodeSessionResponse]:
    """List active transcoding sessions with current progress metrics."""
    manager = get_session_manager(settings)
    sessions = manager.list_sessions()
    return [
        TranscodeSessionResponse(
            id=s.id,
            user_id=s.user_id,
            item_id=s.item_id,
            file_id=s.file_id,
            mode=s.plan.mode,
            is_hardware=s.is_hardware,
            hwaccel=s.plan.hwaccel,
            fps=s.fps,
            speed=s.speed,
            frame=s.frame,
            uptime_seconds=s.uptime_seconds,
            created_at=s.created_at,
            last_activity_at=s.last_activity_at,
            cmd=s.cmd,
        )
        for s in sessions
    ]


@router.post(
    "/transcoding/sessions/{session_id}/stop",
    response_model=TranscodeStopResponse,
)
@router.delete(
    "/transcoding/sessions/{session_id}",
    response_model=TranscodeStopResponse,
)
async def stop_transcoding_session(
    session_id: str,
    _actor: RequireAdmin,
    settings: SettingsDep,
) -> TranscodeStopResponse:
    """Stop and terminate an active transcoding session."""
    manager = get_session_manager(settings)
    stopped = await manager.stop_session(session_id)
    if not stopped:
        raise NotFoundError(message="Transcode session not found")
    return TranscodeStopResponse(status="stopped", session_id=session_id)


@router.get("/transcoding/sessions/{session_id}/logs", response_model=TranscodeLogsResponse)
async def get_transcoding_session_logs(
    session_id: str,
    _actor: RequireAdmin,
    settings: SettingsDep,
) -> TranscodeLogsResponse:
    """Return the recent stderr log lines for an active transcoding session."""
    manager = get_session_manager(settings)
    session = manager.get_session(session_id)
    if not session:
        raise NotFoundError(message="Transcode session not found")
    return TranscodeLogsResponse(
        session_id=session.id,
        lines=list(session.stderr_buffer),
    )


# ------------------------------------------------------------- backup & restore


@router.post(
    "/backup",
    response_class=FileResponse,
    responses={
        200: {
            "description": "Gzipped tar archive with the database snapshot",
            "content": {
                "application/gzip": {"schema": {"type": "string", "format": "binary"}},
            },
        },
    },
)
async def create_database_backup(
    actor: RequireAdmin,
    settings: SettingsDep,
    session: SessionDep,
) -> FileResponse:
    """Trigger an online database backup and download the archive."""
    archive_path = await asyncio.to_thread(create_backup, settings)

    record_audit(
        session,
        actor_id=actor.id,
        action="system.backup",
        target_type="system",
        target_id="database",
        details={"path": str(archive_path), "filename": archive_path.name},
    )
    await session.commit()

    return FileResponse(
        path=str(archive_path),
        media_type="application/gzip",
        filename=archive_path.name,
    )


@router.post(
    "/restore",
    response_model=AdminRestoreResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "description": "Gzipped tar archive containing database snapshot and metadata.",
            "content": {
                "application/gzip": {
                    "schema": {
                        "type": "string",
                        "format": "binary",
                    }
                },
                "application/octet-stream": {
                    "schema": {
                        "type": "string",
                        "format": "binary",
                    }
                },
            },
        }
    },
)
async def restore_database_backup(
    request: Request,
    actor: RequireAdmin,
    settings: SettingsDep,
    session: SessionDep,
    force: bool = Query(default=False),
) -> AdminRestoreResponse:
    """Restore database from an uploaded tar.gz archive stream."""
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_file:
        tmp_path = Path(tmp_file.name)
        try:
            async for chunk in request.stream():
                tmp_file.write(chunk)
            tmp_file.flush()
            metadata = await asyncio.to_thread(
                restore_backup,
                tmp_path,
                settings,
                force=force,
            )
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    # The restore may have replaced the users table with a snapshot that does
    # not contain the acting account (for example, restoring an archive from a
    # different installation). Re-resolve the actor against the restored
    # database; when the account no longer exists the audit entry is recorded
    # as a system action with no actor so the foreign key still holds.
    restored_actor = await session.get(User, actor.id, populate_existing=True)

    record_audit(
        session,
        actor_id=restored_actor.id if restored_actor is not None else None,
        action="system.restore",
        target_type="system",
        target_id="database",
        details={
            "schema_version": metadata.get("schema_version"),
            "hearth_version": metadata.get("hearth_version"),
        },
    )
    await session.commit()

    return AdminRestoreResponse(
        status="restored",
        schema_version=str(metadata.get("schema_version", "unknown")),
        created_at=str(metadata.get("created_at", "")),
        hearth_version=str(metadata.get("hearth_version", "")),
    )
