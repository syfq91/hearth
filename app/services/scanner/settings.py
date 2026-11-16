"""Runtime scanner configuration and database persistence."""

from __future__ import annotations

import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Setting

logger = logging.getLogger("hearth.scanner.settings")

SCAN_INTERVAL_KEY = "scanner.scan_interval_hours"


async def load_scan_interval_override(session: AsyncSession) -> float | None:
    """Return stored scan interval override in hours, or None if not set."""
    row = await session.get(Setting, SCAN_INTERVAL_KEY)
    if row is None:
        return None
    try:
        val = json.loads(row.value)
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
    except (TypeError, ValueError):
        logger.warning(
            "invalid scan_interval_hours value in settings table", extra={"raw": row.value}
        )
    return None


async def save_scan_interval_override(session: AsyncSession, hours: float) -> None:
    """Upsert scan interval override in hours; value is JSON-encoded."""
    row = await session.get(Setting, SCAN_INTERVAL_KEY)
    if row is None:
        session.add(Setting(key=SCAN_INTERVAL_KEY, value=json.dumps(hours)))
    else:
        row.value = json.dumps(hours)


async def apply_scanner_settings(settings: Settings, session: AsyncSession) -> None:
    """Copy stored scanner overrides onto ``settings`` in place."""
    override = await load_scan_interval_override(session)
    if override is not None:
        settings.scan_interval_hours = override
