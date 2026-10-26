"""Tests for the async database foundation and migration pipeline."""

import time
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.config import Settings
from app.core.ids import uuid7, uuid7_timestamp_ms
from app.db.migrate import run_migrations_async
from app.db.session import build_engine, build_sessionmaker


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        env="testing",
        log_level="WARNING",
        data_dir=tmp_path,
        db_path=tmp_path / "hearth.db",
        transcode_dir=tmp_path / "transcode",
        media_dir=tmp_path / "media",
        render_device=tmp_path / "renderD128",
        secret_key="test-secret-key",
    )


def test_uuid7_has_version_variant_and_timestamp() -> None:
    value = uuid7()

    assert len(value) == 36
    assert value[14] == "7"
    assert value[19] in "89ab"
    assert uuid7_timestamp_ms(value) > 0


def test_uuid7_sorts_by_creation_time() -> None:
    first = uuid7()
    time.sleep(0.002)
    second = uuid7()

    assert uuid7_timestamp_ms(first) < uuid7_timestamp_ms(second)
    assert first < second


async def test_migrations_apply_and_are_idempotent(settings: Settings) -> None:
    await run_migrations_async(settings)
    await run_migrations_async(settings)

    engine = build_engine(settings)
    try:
        async with engine.connect() as connection:
            version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        await engine.dispose()

    assert version == "0006_listenbrainz"
    assert settings.db_path.exists()


async def test_sqlite_pragmas_are_applied(settings: Settings) -> None:
    await run_migrations_async(settings)
    engine = build_engine(settings)
    try:
        async with engine.connect() as connection:
            journal_mode = await connection.scalar(text("PRAGMA journal_mode"))
            foreign_keys = await connection.scalar(text("PRAGMA foreign_keys"))
            busy_timeout = await connection.scalar(text("PRAGMA busy_timeout"))
            synchronous = await connection.scalar(text("PRAGMA synchronous"))
    finally:
        await engine.dispose()

    assert journal_mode == "wal"
    assert foreign_keys == 1
    assert busy_timeout == 5000
    assert synchronous == 1


async def test_sessionmaker_returns_working_sessions(settings: Settings) -> None:
    await run_migrations_async(settings)
    engine = build_engine(settings)
    sessionmaker = build_sessionmaker(engine)
    try:
        async with sessionmaker() as session:
            assert await session.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()
