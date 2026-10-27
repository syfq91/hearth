"""Administrator audit-log access."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.deps import RequireAdmin, SettingsDep
from app.core.errors import NotFoundError
from app.db.models import AuditLog
from app.db.session import SessionDep
from app.services.transcode.capabilities import (
    ServerTranscodeCapabilities,
    probe_transcode_capabilities,
)
from app.services.transcode.session import get_session_manager

router = APIRouter(prefix="/admin", tags=["admin"])


class AuditEntry(BaseModel):
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
    session_id: str
    lines: list[str]


@router.get("/audit")
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


@router.get("/transcoding/capabilities")
async def get_transcoding_capabilities(
    _actor: RequireAdmin,
    settings: SettingsDep,
) -> ServerTranscodeCapabilities:
    """Return hardware and software transcoding capabilities."""
    return await probe_transcode_capabilities(settings)


@router.get("/transcoding/sessions")
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


@router.post("/transcoding/sessions/{session_id}/stop")
@router.delete("/transcoding/sessions/{session_id}")
async def stop_transcoding_session(
    session_id: str,
    _actor: RequireAdmin,
    settings: SettingsDep,
) -> dict[str, str]:
    """Stop and terminate an active transcoding session."""
    manager = get_session_manager(settings)
    stopped = await manager.stop_session(session_id)
    if not stopped:
        raise NotFoundError(message="Transcode session not found")
    return {"status": "stopped", "session_id": session_id}


@router.get("/transcoding/sessions/{session_id}/logs")
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
