"""Database backup and restore service using SQLite online backup API."""

import concurrent.futures
import json
import logging
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import __version__
from app.core.config import Settings
from app.core.errors import AppError
from app.db.migrate import run_migrations

logger = logging.getLogger("hearth.backup")


class BackupError(AppError):
    """Raised when backup or restore fails."""

    status_code = 400
    code = "backup_error"


def create_backup(settings: Settings, destination_path: Path | None = None) -> Path:
    """Perform online SQLite backup and pack into a .tar.gz archive with metadata."""
    if not settings.db_path.exists():
        raise BackupError(f"Database file does not exist: {settings.db_path}")

    if destination_path is not None:
        target_path = Path(destination_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backups_dir = settings.data_dir / "backups"
        backups_dir.mkdir(parents=True, exist_ok=True)
        target_path = backups_dir / f"hearth_backup_{timestamp}.tar.gz"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir_path = Path(tmpdir)
        temp_db_path = tmp_dir_path / "hearth.db"

        # 1. Online sqlite backup: non-blocking snapshot
        src_conn = sqlite3.connect(str(settings.db_path.resolve()))
        dest_conn = sqlite3.connect(str(temp_db_path))
        try:
            with dest_conn:
                src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            src_conn.close()

        # 2. Extract schema version from the backup snapshot
        schema_version = "unknown"
        inspect_conn = sqlite3.connect(str(temp_db_path))
        try:
            cur = inspect_conn.cursor()
            cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
            row = cur.fetchone()
            if row and row[0]:
                schema_version = str(row[0])
        except Exception as exc:
            logger.warning("Could not read alembic_version from backup: %s", exc)
        finally:
            inspect_conn.close()

        # 3. Write metadata.json
        metadata: dict[str, Any] = {
            "hearth_version": __version__,
            "schema_version": schema_version,
            "created_at": datetime.now(UTC).isoformat(),
            "db_size_bytes": temp_db_path.stat().st_size,
        }
        meta_path = tmp_dir_path / "metadata.json"
        meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        # 4. Pack into .tar.gz
        temp_tar_path = tmp_dir_path / "archive.tar.gz"
        with tarfile.open(temp_tar_path, "w:gz") as tar:
            tar.add(temp_db_path, arcname="hearth.db")
            tar.add(meta_path, arcname="metadata.json")

        shutil.move(str(temp_tar_path), str(target_path))

    logger.info("Backup successfully written to %s", target_path)
    return target_path


def restore_backup(
    archive_path: Path,
    settings: Settings,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Validate archive, extract database snapshot, and restore to settings.db_path."""
    archive_path = Path(archive_path).resolve()
    if not archive_path.exists():
        raise BackupError(f"Backup file not found: {archive_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_dir_path = Path(tmpdir)
        try:
            with tarfile.open(archive_path, "r:gz") as tar:
                # Path traversal guard
                for member in tar.getmembers():
                    if member.name.startswith("/") or ".." in member.name:
                        raise BackupError("Archive contains unsafe file paths")
                tar.extractall(path=tmp_dir_path)
        except tarfile.TarError as exc:
            raise BackupError(f"Invalid tar.gz archive: {exc}") from exc

        temp_db_path = tmp_dir_path / "hearth.db"
        meta_path = tmp_dir_path / "metadata.json"

        if not temp_db_path.exists():
            raise BackupError("Backup archive missing hearth.db")
        if not meta_path.exists():
            raise BackupError("Backup archive missing metadata.json")

        try:
            metadata: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise BackupError(f"Corrupt metadata.json in archive: {exc}") from exc

        # Verify SQLite integrity
        test_conn = sqlite3.connect(str(temp_db_path))
        try:
            cur = test_conn.cursor()
            cur.execute("PRAGMA integrity_check")
            result = cur.fetchone()
            if not result or result[0] != "ok":
                raise BackupError(f"Corrupted database in backup: {result}")
        finally:
            test_conn.close()

        # Destination paths
        dest_db = settings.db_path.resolve()
        dest_db.parent.mkdir(parents=True, exist_ok=True)

        # Restore database using SQLite backup API if file exists, or copy
        if dest_db.exists():
            dest_conn = sqlite3.connect(str(dest_db))
            src_conn = sqlite3.connect(str(temp_db_path))
            try:
                with dest_conn:
                    src_conn.backup(dest_conn)
            finally:
                src_conn.close()
                dest_conn.close()
        else:
            shutil.copy2(temp_db_path, dest_db)

        # Run forward migrations if needed (executed in a thread to isolate asyncio.run)
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(run_migrations, settings).result()
        except Exception as exc:
            logger.warning("Error running migrations after restore: %s", exc)

    logger.info("Database successfully restored from %s", archive_path)
    return metadata
