"""Migration compatibility and schema upgrade tests.

Verifies that incremental schema upgrades preserve pre-existing data across
all version milestones from initial baseline up to the latest revision.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.db.migrate import ALEMBIC_INI, MIGRATIONS_DIR


def _make_test_alembic_config(db_path: Path) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    db_url = f"sqlite+aiosqlite:///{db_path}"
    config.attributes["database_url"] = db_url
    config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))
    return config


@pytest.fixture
def migration_db(tmp_path: Path) -> Iterator[Path]:
    db_path = tmp_path / "migration_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()
    yield db_path
    if db_path.exists():
        db_path.unlink()


def test_clean_upgrade_to_head(migration_db: Path) -> None:
    """Verifies a fresh empty database can be upgraded directly to head."""
    cfg = _make_test_alembic_config(migration_db)
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(str(migration_db))
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        expected_tables = {
            "alembic_version",
            "users",
            "libraries",
            "media_items",
            "media_files",
            "series",
            "seasons",
            "episodes",
            "artists",
            "albums",
            "tracks",
            "authors",
            "books",
            "book_progress",
            "collections",
            "collection_items",
        }
        for table in expected_tables:
            assert table in tables, f"Expected table {table} missing after upgrade"
    finally:
        conn.close()


def test_incremental_upgrade_preserves_data(migration_db: Path) -> None:
    """Verifies that upgrading across releases preserves existing records."""
    cfg = _make_test_alembic_config(migration_db)

    # 1. Upgrade to early schema (Phase 1 & 2 models)
    command.upgrade(cfg, "0003_media_catalog")

    # 2. Seed data in early schema tables
    conn = sqlite3.connect(str(migration_db))
    try:
        conn.execute(
            """
            INSERT INTO users (
                id, username, password_hash, role, disabled,
                allow_transcode, token_version, created_at, updated_at
            )
            VALUES (
                'user-1', 'initial_admin', 'hash123', 'admin', 0,
                1, 0, '2026-10-21 12:00:00', '2026-10-21 12:00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO libraries (id, name, type, created_at, updated_at)
            VALUES (
                'lib-1', 'Initial Movies', 'movie',
                '2026-10-21 12:00:00', '2026-10-21 12:00:00'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO media_items (
                id, library_id, type, title, sort_title, added_at,
                match_state, match_attempts, provider_ids,
                created_at, updated_at
            )
            VALUES (
                'item-1', 'lib-1', 'movie', 'Initial Movie Title',
                'initial movie title', '2026-10-21 12:00:00',
                'unmatched', 0, '{}',
                '2026-10-21 12:00:00', '2026-10-21 12:00:00'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    # 3. Apply intermediate and final migrations step by step
    command.upgrade(cfg, "0004_tv_shows")
    command.upgrade(cfg, "0005_music")
    command.upgrade(cfg, "0006_listenbrainz")
    command.upgrade(cfg, "0007_books")
    command.upgrade(cfg, "head")  # 0008_collections_and_content_rating

    # 4. Verify existing records survive with integrity
    conn = sqlite3.connect(str(migration_db))
    try:
        user_row = conn.execute(
            """
            SELECT id, username, role, token_version, listenbrainz_token
            FROM users WHERE id = 'user-1'
            """
        ).fetchone()
        assert user_row is not None
        assert user_row[0] == "user-1"
        assert user_row[1] == "initial_admin"
        assert user_row[2] == "admin"
        assert user_row[3] == 0
        assert user_row[4] is None  # Added in 0006_listenbrainz

        lib_row = conn.execute("SELECT id, name, type FROM libraries WHERE id = 'lib-1'").fetchone()
        assert lib_row is not None
        assert lib_row[1] == "Initial Movies"

        item_row = conn.execute(
            "SELECT id, title, content_rating FROM media_items WHERE id = 'item-1'"
        ).fetchone()
        assert item_row is not None
        assert item_row[1] == "Initial Movie Title"
        assert item_row[2] is None  # Added in 0008_collections_and_content_rating

        # Check collections table exists and can accept rows
        conn.execute(
            """
            INSERT INTO collections (id, name, created_at, updated_at)
            VALUES ('col-1', 'Action Classics', '2026-11-03 10:00:00', '2026-11-03 10:00:00')
            """
        )
        conn.commit()
        col_count = conn.execute("SELECT count(*) FROM collections").fetchone()[0]
        assert col_count == 1
    finally:
        conn.close()


def test_migration_downgrade_and_reupgrade(migration_db: Path) -> None:
    """Verifies that schema can downgrade and re-upgrade cleanly without errors."""
    cfg = _make_test_alembic_config(migration_db)

    # Upgrade to head
    command.upgrade(cfg, "head")

    # Downgrade 2 steps (back to 0006_listenbrainz)
    command.downgrade(cfg, "0006_listenbrainz")

    conn = sqlite3.connect(str(migration_db))
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        # Books and collections should not exist at 0006
        assert "books" not in tables
        assert "collections" not in tables
        assert "tracks" in tables
    finally:
        conn.close()

    # Re-upgrade to head
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(str(migration_db))
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "books" in tables
        assert "collections" in tables
    finally:
        conn.close()
