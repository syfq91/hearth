"""Administrator audit-log access."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import RequireAdmin, SettingsDep
from app.db.models import AuditLog
from app.db.session import SessionDep
from app.services.transcode.capabilities import (
    ServerTranscodeCapabilities,
    probe_transcode_capabilities,
)

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
