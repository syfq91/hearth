"""Verify that the generated TypeScript client artifact is present and valid."""

from scripts.generate_client import CLIENT_OUTPUT, SCHEMA_INPUT


def test_generated_client_artifact_exists_and_matches() -> None:
    assert SCHEMA_INPUT.exists(), "docs/openapi.json must exist"
    assert CLIENT_OUTPUT.exists(), (
        "packages/client/src/schema.d.ts is missing; run scripts/generate_client.py"
    )

    content = CLIENT_OUTPUT.read_text(encoding="utf-8")
    assert "export interface paths" in content
    assert "/api/v1/auth/login" in content
    assert "/api/v1/items/{item_id}" in content
    assert "/api/v1/search" in content
