"""Integration tests for SQLite online backup and restore service, CLI, and API."""

import sqlite3
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typer.testing import CliRunner

from app.cli import app as cli_app
from app.core.config import Settings
from app.db.migrate import run_migrations
from app.db.models import Library
from app.services.backup import create_backup, restore_backup

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    res = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert res.status_code == 201
    return res.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


@pytest.mark.asyncio
async def test_service_backup_and_restore(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession], tmp_path: Path
) -> None:
    # 1. Seed catalog data
    async with db_sessionmaker() as session:
        lib1 = Library(name="Movies Before Backup", type="movie")
        lib2 = Library(name="Series Before Backup", type="tv")
        session.add_all([lib1, lib2])
        await session.commit()

    # 2. Perform online backup
    archive_path = tmp_path / "test_backup.tar.gz"
    res_path = create_backup(db_settings, destination_path=archive_path)
    assert res_path.exists()
    assert res_path.stat().st_size > 0

    # 3. Mutate catalog
    async with db_sessionmaker() as session:
        lib3 = Library(name="Added After Backup", type="movie")
        session.add(lib3)
        await session.commit()

    # 4. Restore database snapshot
    meta = restore_backup(archive_path, db_settings)
    assert meta["schema_version"] != "unknown"

    # 5. Query restored database directly
    conn = sqlite3.connect(str(db_settings.db_path))
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM libraries ORDER BY name ASC")
        names = [row[0] for row in cur.fetchall()]
        assert "Movies Before Backup" in names
        assert "Series Before Backup" in names
        assert "Added After Backup" not in names
    finally:
        conn.close()


def test_cli_backup_and_restore(
    monkeypatch: pytest.MonkeyPatch, db_settings: Settings, tmp_path: Path
) -> None:
    runner = CliRunner()
    run_migrations(db_settings)
    monkeypatch.setattr("app.cli.get_settings", lambda: db_settings)

    backup_dest = tmp_path / "cli_backup.tar.gz"
    # CLI backup
    res = runner.invoke(cli_app, ["backup", "--output", str(backup_dest)])
    assert res.exit_code == 0
    assert backup_dest.exists()
    assert "Backup created successfully" in res.stdout

    # CLI restore
    res_restore = runner.invoke(cli_app, ["restore", "--input", str(backup_dest)])
    assert res_restore.exit_code == 0
    assert "Database successfully restored" in res_restore.stdout


@pytest.mark.asyncio
async def test_admin_api_backup_and_restore(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tokens = await _setup(api_client)
    admin_hdr = _bearer(tokens)

    # Add a library
    async with db_sessionmaker() as session:
        lib = Library(name="API Backup Library", type="movie")
        session.add(lib)
        await session.commit()

    # Create a viewer user
    u_res = await api_client.post(
        "/api/v1/users",
        headers=admin_hdr,
        json={"username": "viewer", "password": "correct horse", "role": "user"},
    )
    assert u_res.status_code == 201
    login_res = await api_client.post(
        "/api/v1/auth/login",
        json={"username": "viewer", "password": "correct horse"},
    )
    viewer_hdr = _bearer(login_res.json())

    # 1. Non-admin forbidden
    bad_backup = await api_client.post("/api/v1/admin/backup", headers=viewer_hdr)
    assert bad_backup.status_code == 403

    # 2. Admin backup download
    backup_res = await api_client.post("/api/v1/admin/backup", headers=admin_hdr)
    assert backup_res.status_code == 200
    assert backup_res.headers["content-type"] in {"application/gzip", "application/x-gzip"}
    archive_bytes = backup_res.content
    assert len(archive_bytes) > 0

    # 3. Non-admin restore forbidden
    bad_restore = await api_client.post(
        "/api/v1/admin/restore",
        headers={**viewer_hdr, "Content-Type": "application/gzip"},
        content=archive_bytes,
    )
    assert bad_restore.status_code == 403

    # 4. Admin restore upload
    restore_res = await api_client.post(
        "/api/v1/admin/restore",
        headers={**admin_hdr, "Content-Type": "application/gzip"},
        content=archive_bytes,
    )
    assert restore_res.status_code == 200
    data = restore_res.json()
    assert data["status"] == "restored"
    assert data["schema_version"] != ""
