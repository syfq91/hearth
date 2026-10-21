"""Async SQLAlchemy engine, SQLite pragmas and request-scoped sessions."""

from collections.abc import AsyncIterator
from typing import Annotated, Any, cast

from fastapi import Depends, Request
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings

# One writer at a time is a SQLite invariant. Keep the busy timeout generous
# enough for short concurrent writes but do not hold transactions across I/O.
BUSY_TIMEOUT_MS = 5000


def _apply_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Configure durability and concurrency on every new SQLite connection."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def build_engine(settings: Settings) -> AsyncEngine:
    """Create the async engine and register SQLite connection pragmas.

    Creating an engine does not open the database, so this is safe to call
    before the schema exists (health checks stay read-only).
    """
    engine = create_async_engine(
        settings.database_url,
        future=True,
        pool_pre_ping=True,
        connect_args={"timeout": BUSY_TIMEOUT_MS / 1000},
    )
    event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory with expired-on-commit objects refreshed on use."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield one transaction-scoped session per request."""
    sessionmaker = cast(async_sessionmaker[AsyncSession], request.app.state.sessionmaker)
    async with sessionmaker() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
