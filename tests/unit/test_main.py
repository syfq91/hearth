"""Tests for the FastAPI application factory and system endpoints."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.errors import NotFoundError
from app.core.system import FFmpegHealth, VaapiHealth
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings isolated to a temporary directory with no DB file."""
    return Settings(
        env="testing",
        log_level="WARNING",
        data_dir=tmp_path,
        db_path=tmp_path / "hearth.db",
        transcode_dir=tmp_path / "transcode",
        media_dir=tmp_path / "media",
        render_device=tmp_path / "renderD128",
    )


@pytest.fixture
def client(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Test client with fast, deterministic runtime probes."""
    application = create_app(settings)

    async def fake_ffmpeg(*_args: object, **_kwargs: object) -> FFmpegHealth:
        return FFmpegHealth(status="ok", version="6.1.1-test")

    async def fake_vaapi(*_args: object, **_kwargs: object) -> VaapiHealth:
        return VaapiHealth(
            status="unavailable",
            device=str(settings.render_device),
            detail="render device not present",
        )

    import app.api.v1.system as system_module

    monkeypatch.setattr(system_module, "probe_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(system_module, "probe_vaapi", fake_vaapi)

    yield TestClient(application)


def test_health_reports_runtime_components(client: TestClient) -> None:
    """Health returns version, DB state, ffmpeg version and VAAPI state."""
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"] == "0.1.0"
    assert payload["database"]["status"] == "not_initialized"
    assert payload["ffmpeg"] == {
        "status": "ok",
        "detail": None,
        "version": "6.1.1-test",
    }
    assert payload["vaapi"]["status"] == "unavailable"
    assert payload["vaapi"]["device"].endswith("renderD128")


def test_health_degrades_when_ffmpeg_is_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing ffmpeg must be visible as degraded health, not a stack trace."""
    import app.api.v1.system as system_module

    async def missing_ffmpeg(*_args: object, **_kwargs: object) -> FFmpegHealth:
        return FFmpegHealth(status="unavailable", detail="ffmpeg not found")

    monkeypatch.setattr(system_module, "probe_ffmpeg", missing_ffmpeg)

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["ffmpeg"]["status"] == "unavailable"


def test_health_reports_database_error(client: TestClient, settings: Settings) -> None:
    """A corrupt database file is reported as an error and degrades health."""
    settings.db_path.write_text("not a sqlite database", encoding="utf-8")

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["database"]["status"] == "error"


def test_version_endpoint(client: TestClient) -> None:
    """Version endpoint reports the package version and API generation."""
    response = client.get("/api/v1/version")

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "hearth"
    assert payload["version"] == "0.1.0"
    assert payload["api_version"] == "v1"
    assert payload["python_version"]


def test_request_id_is_generated_and_returned(client: TestClient) -> None:
    """Every response carries a generated request ID."""
    response = client.get("/api/v1/version")

    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32


def test_valid_request_id_is_echoed(client: TestClient) -> None:
    """A safe caller-supplied request ID is preserved for log correlation."""
    response = client.get("/api/v1/version", headers={"X-Request-ID": "abc-123"})

    assert response.headers["x-request-id"] == "abc-123"


def test_unsafe_request_id_is_replaced(client: TestClient) -> None:
    """Untrusted header content is never reflected into logs or responses."""
    response = client.get("/api/v1/version", headers={"X-Request-ID": "bad value"})

    assert response.headers["x-request-id"] != "bad value"
    assert len(response.headers["x-request-id"]) == 32


def test_unknown_route_uses_unified_error_shape(client: TestClient) -> None:
    """Framework 404s use the same envelope as application errors."""
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "Not Found", "details": {}}
    }


def test_app_error_handler_uses_unified_shape(settings: Settings) -> None:
    """Custom AppError subclasses are serialized centrally."""
    application = create_app(settings)

    @application.get("/_test/not-found")
    async def raise_not_found() -> None:
        raise NotFoundError("Missing thing", details={"id": "42"})

    with TestClient(application) as client:
        response = client.get("/_test/not-found")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Missing thing",
            "details": {"id": "42"},
        }
    }


def test_validation_error_handler_uses_unified_shape(settings: Settings) -> None:
    """FastAPI validation errors are mapped to the unified envelope."""
    application = create_app(settings)

    @application.get("/_test/count")
    async def count_items(count: int) -> dict[str, int]:
        return {"count": count}

    with TestClient(application) as client:
        response = client.get("/_test/count", params={"count": "nope"})

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["message"] == "Invalid request parameters"
    assert payload["error"]["details"]["errors"]


def test_unhandled_exception_returns_safe_error(settings: Settings) -> None:
    """Unexpected exceptions never leak internals to the client."""
    application = create_app(settings)

    @application.get("/_test/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/_test/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_server_error",
            "message": "An unexpected error occurred",
            "details": {},
        }
    }
    assert "secret internal detail" not in response.text


def test_health_does_not_create_database(client: TestClient, settings: Settings) -> None:
    """Health checks are read-only and never initialize the schema."""
    client.get("/api/v1/health")

    assert not settings.db_path.exists()


def test_app_factory_returns_fresh_application(settings: Settings) -> None:
    """Each factory call builds an independent app instance."""
    first = create_app(settings)
    second = create_app(settings)

    assert isinstance(first, FastAPI)
    assert first is not second
