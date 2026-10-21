"""Shared job types passed between the worker and its handlers."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings


class JobCancelled(Exception):
    """Raised by a handler when it observes a cancellation request."""


@dataclass
class JobContext:
    """Everything a handler needs without exposing the worker internals."""

    job_id: str
    payload: dict[str, Any]
    report_progress: Callable[[float], Awaitable[None]]
    is_cancelled: Callable[[], Awaitable[bool]]
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]


JobHandler = Callable[[JobContext], Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True)
class JobSpec:
    """Handler plus its per-type concurrency limit."""

    handler: JobHandler
    concurrency: int = 1
