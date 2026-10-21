"""System endpoints: health and version."""

import asyncio
import platform
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.core.deps import SettingsDep
from app.core.system import (
    ComponentHealth,
    FFmpegHealth,
    VaapiHealth,
    check_database,
    probe_ffmpeg,
    probe_vaapi,
)

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    """Aggregate runtime health.

    ``status`` is ``ok`` when the server can serve requests: ffmpeg is usable
    and the database is either ready or not yet initialized. VAAPI is optional
    and never degrades health; it is reported so operators can verify hardware
    passthrough.
    """

    status: Literal["ok", "degraded"]
    version: str
    database: ComponentHealth
    ffmpeg: FFmpegHealth
    vaapi: VaapiHealth


class VersionResponse(BaseModel):
    """Build and runtime version information."""

    name: str
    version: str
    api_version: str
    python_version: str


@router.get("/health")
async def health(settings: SettingsDep) -> HealthResponse:
    """Report application, database, ffmpeg and VAAPI status."""
    ffmpeg, vaapi, database = await asyncio.gather(
        probe_ffmpeg(),
        probe_vaapi(settings.render_device),
        asyncio.to_thread(check_database, settings.db_path),
    )

    healthy = ffmpeg.status == "ok" and database.status in {"ok", "not_initialized"}
    return HealthResponse(
        status="ok" if healthy else "degraded",
        version=__version__,
        database=database,
        ffmpeg=ffmpeg,
        vaapi=vaapi,
    )


@router.get("/version")
async def version() -> VersionResponse:
    """Report the application version and API generation."""
    return VersionResponse(
        name="hearth",
        version=__version__,
        api_version="v1",
        python_version=platform.python_version(),
    )
