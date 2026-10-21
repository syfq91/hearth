"""Tests for the Typer command-line interface."""

from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

import app.cli as cli
from app.cli import app as cli_app

runner = CliRunner()


def test_help_lists_phase_zero_commands() -> None:
    """The CLI exposes api, worker, migrate and create-admin."""
    result = runner.invoke(cli_app, ["--help"])

    assert result.exit_code == 0
    for command in ("api", "worker", "migrate", "create-admin"):
        assert command in result.stdout


def test_bare_invocation_shows_help() -> None:
    """Bare invocation shows help rather than running a command."""
    result = runner.invoke(cli_app, [])

    assert result.exit_code == 2  # no_args_is_help exits like Click's UsageError
    assert "Usage" in result.stdout or "Usage" in (result.stderr or "")


def test_api_passes_settings_to_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    """The api command binds to settings/env overrides and disables duplicates."""
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_run(target: str, **kwargs: Any) -> None:
        calls.append((target, kwargs))

    monkeypatch.setattr(cli.uvicorn, "run", fake_run)

    result = runner.invoke(cli_app, ["api", "--port", "9001", "--reload"])

    assert result.exit_code == 0
    assert len(calls) == 1
    target, kwargs = calls[0]
    assert target == "app.main:app"
    assert kwargs["port"] == 9001
    assert kwargs["reload"] is True
    assert kwargs["log_config"] is None
    assert kwargs["access_log"] is False


def test_worker_enters_idle_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker command starts the asyncio idle loop until signalled."""
    executed: list[str] = []

    def fake_run(coroutine: Any) -> None:
        executed.append("ran")
        coroutine.close()

    monkeypatch.setattr(cli.asyncio, "run", fake_run)

    result = runner.invoke(cli_app, ["worker"])

    assert result.exit_code == 0
    assert executed == ["ran"]


def test_migrate_applies_migrations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """migrate applies the schema to the configured database."""
    from app.core.config import Settings

    settings = Settings(
        env="testing",
        log_level="WARNING",
        data_dir=tmp_path,
        db_path=tmp_path / "hearth.db",
        media_dir=tmp_path / "media",
        secret_key="test-secret-key",
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    result = runner.invoke(cli_app, ["migrate"])

    assert result.exit_code == 0
    assert settings.db_path.exists()


def test_create_admin_fails_until_phase_one() -> None:
    """The stub must not signal success for an unimplemented admin creation."""
    result = runner.invoke(cli_app, ["create-admin", "--username", "admin"])

    assert result.exit_code == 1
    assert "not implemented until Phase 1" in result.stderr


def test_reserved_name_is_exported() -> None:
    """The Typer app is the pyproject entrypoint target."""
    assert isinstance(cli_app, typer.Typer)
