"""Database migration entrypoint shared by the API, worker and CLI."""

import asyncio
import fcntl
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import Settings

logger = logging.getLogger("hearth.db.migrations")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def make_alembic_config(settings: Settings) -> Config:
    """Build an Alembic config pointed at our scripts and database."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # Pass the runtime settings through attributes so env.py does not fall back
    # to the process-wide cached settings (important for tests).
    config.attributes["database_url"] = settings.database_url
    # Alembic treats '%' as interpolation syntax; escape it for file URLs.
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    return config


def upgrade_to_head(settings: Settings) -> None:
    """Apply all migrations. Safe to call concurrently; callers hold the lock."""
    command.upgrade(make_alembic_config(settings), "head")
    logger.info("database migrations applied")


def run_migrations(settings: Settings) -> None:
    """Apply migrations while holding an inter-process startup lock.

    Multiple API replicas or an API plus worker may start together. SQLite
    permits only one writer, so serialise schema changes with an advisory file
    lock before touching Alembic.
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = settings.data_dir / ".migrations.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            upgrade_to_head(settings)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


async def run_migrations_async(settings: Settings) -> None:
    """Run migrations off the event loop.

    Alembic's async environment owns its event loop, so it must not be invoked
    from an already-running loop in the same thread.
    """
    await asyncio.to_thread(run_migrations, settings)
