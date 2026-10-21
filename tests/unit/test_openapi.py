"""The committed OpenAPI document must match the application schema."""

from scripts.export_openapi import OUTPUT, render_schema


def test_committed_openapi_schema_is_current() -> None:
    assert OUTPUT.exists(), "docs/openapi.json is missing; run scripts/export_openapi.py"
    assert OUTPUT.read_text(encoding="utf-8") == render_schema()


def test_openapi_documents_all_phase_one_routes() -> None:
    import json

    schema = json.loads(render_schema())
    paths = schema["paths"]

    for path in (
        "/api/v1/auth/setup",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/auth/media-tokens",
        "/api/v1/libraries",
        "/api/v1/libraries/{library_id}",
        "/api/v1/users",
        "/api/v1/jobs",
        "/api/v1/events",
        "/api/v1/admin/audit",
    ):
        assert path in paths
