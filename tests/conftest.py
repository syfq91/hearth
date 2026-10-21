"""Shared pytest fixtures: isolated settings, migrated databases and API client."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.migrate import run_migrations_async
from app.db.session import build_engine, build_sessionmaker
from app.main import create_app


@pytest.fixture
def db_settings(tmp_path: Path) -> Settings:
    """Fully isolated settings with a temporary database and media root."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    return Settings(
        env="testing",
        log_level="WARNING",
        data_dir=tmp_path,
        db_path=tmp_path / "hearth.db",
        transcode_dir=tmp_path / "transcode",
        media_dir=media_dir,
        render_device=tmp_path / "renderD128",
        secret_key="test-secret-key",
        job_poll_interval=0.05,
    )


@pytest.fixture
async def db_sessionmaker(db_settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A migrated database and its session factory."""
    await run_migrations_async(db_settings)
    engine = build_engine(db_settings)
    yield build_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
async def db_session(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with db_sessionmaker() as session:
        yield session


@pytest.fixture
async def api_client(db_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client with a started application lifespan and fresh schema."""
    application = create_app(db_settings)
    async with LifespanManager(application):
        transport = httpx.ASGITransport(app=application)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
