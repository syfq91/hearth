"""System endpoints: health and version."""

import asyncio
import platform
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.core.deps import SettingsDep
from app.core.locks import LoopAwareLock
from app.core.system import (
    ComponentHealth,
    FFmpegHealth,
    VaapiHealth,
    check_database,
    probe_ffmpeg,
    probe_vaapi,
)

router = APIRouter(tags=["system"])

# Health probes spawn subprocesses. Cache successful results for 30 s and
# failures for 5 s so a container healthcheck cannot repeatedly start ffmpeg
# or vainfo, while a broken tool is noticed again quickly.
_HEALTH_CACHE_TTL_SECONDS = 30.0
_HEALTH_FAILURE_TTL_SECONDS = 5.0
_ProbeKey = tuple[Callable[..., Awaitable[Any]], tuple[object, ...]]
_PROBE_CACHE: dict[_ProbeKey, tuple[float, ComponentHealth]] = {}
_PROBE_LOCKS: dict[_ProbeKey, LoopAwareLock] = {}
_PROBE_REGISTRY_LOCK = LoopAwareLock()


def reset_health_probe_cache() -> None:
    """Clear cached probe results (tests and app reloads call this explicitly)."""
    _PROBE_CACHE.clear()
    _PROBE_LOCKS.clear()


async def _cached_probe[T: ComponentHealth](probe: Callable[..., Awaitable[T]], *args: object) -> T:
    """Return a recent probe result, running the probe at most once per key.

    The probe function is part of the key so replacing it (monkeypatching in
    tests, or reloading the module) cannot return another implementation's
    result. TTL values are read at call time so tests can shorten them.
    """
    key = (probe, args)
    cached = _PROBE_CACHE.get(key)
    if cached is not None and time.monotonic() < cached[0]:
        return cast(T, cached[1])

    # One lock per probe key: callers of the same probe share the in-flight
    # run, while different probes (ffmpeg vs VAAPI) still execute concurrently.
    async with _PROBE_REGISTRY_LOCK:
        probe_lock = _PROBE_LOCKS.setdefault(key, LoopAwareLock())

    async with probe_lock:
        cached = _PROBE_CACHE.get(key)
        if cached is not None and time.monotonic() < cached[0]:
            return cast(T, cached[1])
        result = await probe(*args)
        ttl = _HEALTH_CACHE_TTL_SECONDS if result.status == "ok" else _HEALTH_FAILURE_TTL_SECONDS
        async with _PROBE_REGISTRY_LOCK:
            _PROBE_CACHE[key] = (time.monotonic() + ttl, result)
        return result


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


class Attribution(BaseModel):
    """Required attribution for a metadata source."""

    provider: str
    notice: str
    url: str
    logo_url: str | None = None


class AboutResponse(BaseModel):
    """Application identity and third-party notices."""

    name: str
    version: str
    description: str
    attributions: list[Attribution]


TMDB_NOTICE = "This product uses the TMDB API but is not endorsed or certified by TMDB."
TMDB_LOGO_URL = (
    "https://www.themoviedb.org/assets/2/v4/logos/v2/"
    "blue_short-8e7b30f73a4020692ccca9c88bafe5dcb6f8a62a4c6bc55cd9ba82bb2cd95f6c.svg"
)


@router.get("/health")
async def health(settings: SettingsDep) -> HealthResponse:
    """Report application, database, ffmpeg and VAAPI status."""
    ffmpeg, vaapi, database = await asyncio.gather(
        _cached_probe(probe_ffmpeg),
        _cached_probe(probe_vaapi, settings.render_device),
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


@router.get("/about")
async def about() -> AboutResponse:
    """Report application identity and required third-party notices."""
    return AboutResponse(
        name="Hearth",
        version=__version__,
        description="A self-hosted personal media server.",
        attributions=[
            Attribution(
                provider="TMDB",
                notice=TMDB_NOTICE,
                url="https://www.themoviedb.org/",
                logo_url=TMDB_LOGO_URL,
            )
        ],
    )
