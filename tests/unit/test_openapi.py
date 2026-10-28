"""The committed OpenAPI document must match the application schema."""

import json
from typing import Any

from scripts.export_openapi import OUTPUT, render_schema

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
_TYPED_KEYS = ("$ref", "oneOf", "anyOf", "allOf", "type", "properties", "items")


def _is_typed(schema: dict[str, Any]) -> bool:
    """Return whether an OpenAPI schema carries real type information."""
    return any(key in schema for key in _TYPED_KEYS)


def test_committed_openapi_schema_is_current() -> None:
    assert OUTPUT.exists(), "docs/openapi.json is missing; run scripts/export_openapi.py"
    assert OUTPUT.read_text(encoding="utf-8") == render_schema()


def test_no_untyped_success_responses() -> None:
    """Every documented 2xx response must carry a concrete schema.

    Endpoints annotated ``-> Any`` produce a schema stub with no type
    information; Phase 8 requires the whole route surface to be typed.
    """
    schema = json.loads(render_schema())
    untyped: list[str] = []
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method not in HTTP_METHODS:
                continue
            for code, response in operation.get("responses", {}).items():
                if not code.startswith("2") or code in ("204", "304"):
                    continue
                content = response.get("content") or {}
                schemas = [
                    media.get("schema", {}) for media in content.values() if isinstance(media, dict)
                ]
                if not any(_is_typed(s) for s in schemas):
                    untyped.append(f"{method.upper()} {path} {code}")
    assert untyped == [], (
        "operations with untyped (Any) success responses; annotate the endpoint "
        f"return type or document `responses`: {sorted(set(untyped))}"
    )


def test_openapi_documents_all_phase_one_routes() -> None:
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
