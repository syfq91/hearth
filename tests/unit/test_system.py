"""Tests for runtime dependency probes."""

import sqlite3
import sys
from pathlib import Path

from app.core.system import check_database, probe_ffmpeg, probe_vaapi


async def test_probe_ffmpeg_reports_missing_binary() -> None:
    """A missing ffmpeg is unavailable, not an exception."""
    result = await probe_ffmpeg(binary="hearth-definitely-not-installed")

    assert result.status == "unavailable"
    assert result.version is None
    assert "not found" in (result.detail or "")


async def test_probe_vaapi_reports_missing_device(tmp_path: Path) -> None:
    """A host without a render node reports VAAPI unavailable."""
    result = await probe_vaapi(tmp_path / "renderD128")

    assert result.status == "unavailable"
    assert result.detail == "render device not present"
    assert result.device.endswith("renderD128")


async def test_probe_vaapi_parses_successful_output(tmp_path: Path) -> None:
    """vainfo output is parsed for the driver and profile presence."""
    device = tmp_path / "renderD128"
    device.touch()
    fake_vainfo = _write_script(
        tmp_path,
        "fake_vainfo",
        "\n".join(
            [
                "vainfo: VA-API version: 1.20 (libva 2.20.0)",
                "vainfo: Driver version: Intel iHD driver for Intel(R) Gen Graphics - 24.1.0",
                "vainfo: Supported profile and entrypoints",
                "      VAProfileH264High               : VAEntrypointVLD",
            ]
        ),
    )

    result = await probe_vaapi(device, binary=str(fake_vainfo))

    assert result.status == "ok"
    assert result.driver == "Intel iHD driver for Intel(R) Gen Graphics - 24.1.0"


async def test_probe_vaapi_reports_failed_probe(tmp_path: Path) -> None:
    """A failing vainfo run is unavailable and preserves the first error line."""
    device = tmp_path / "renderD128"
    device.touch()
    fake_vainfo = _write_script(
        tmp_path,
        "failing_vainfo",
        "vainfo: VA-API version: 1.20 (libva 2.20.0)\nvainfo: Failed to open connection",
        exit_code=1,
    )

    result = await probe_vaapi(device, binary=str(fake_vainfo))

    assert result.status == "unavailable"
    assert "Failed to open connection" in (result.detail or "")


def test_check_database_missing_file(tmp_path: Path) -> None:
    """A missing DB file is not initialized, which is expected in Phase 0."""
    result = check_database(tmp_path / "hearth.db")

    assert result.status == "not_initialized"


def test_check_database_valid_file(tmp_path: Path) -> None:
    """An existing SQLite database is reported as ok without mutation."""
    db_path = tmp_path / "hearth.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE example (id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()

    result = check_database(db_path)

    assert result.status == "ok"
    assert db_path.read_bytes().startswith(b"SQLite format 3")


def test_check_database_corrupt_file(tmp_path: Path) -> None:
    """A non-database file is reported as an error rather than crashing."""
    db_path = tmp_path / "hearth.db"
    db_path.write_text("definitely not sqlite", encoding="utf-8")

    result = check_database(db_path)

    assert result.status == "error"
    assert "database check failed" in (result.detail or "")


def _write_script(tmp_path: Path, name: str, output: str, exit_code: int = 0) -> Path:
    """Create an executable Python script that mimics a CLI tool."""
    script = tmp_path / name
    script.write_text(
        f"#!{sys.executable}\nprint({output!r})\nraise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script
