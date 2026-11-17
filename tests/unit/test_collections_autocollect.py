"""Tests for the collection auto-collect query."""

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.collections import _auto_collect_item_ids


class _FakeResult:
    def __init__(self, rows: list[tuple[str, dict[str, Any] | None]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[str, dict[str, Any] | None]]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[tuple[str, dict[str, Any] | None]]) -> None:
        self.rows = rows
        self.executed: Any = None

    async def execute(self, statement: Any) -> _FakeResult:
        self.executed = statement
        return _FakeResult(self.rows)


async def test_auto_collect_uses_scalar_projection_and_matches_both_keys() -> None:
    """Only id/provider_ids are selected, and both provider keys are honored."""
    session = _FakeSession(
        [
            ("m1", {"tmdb_collection_id": 119}),
            ("m2", {"collection_id": "119"}),
            ("m3", {"tmdb_collection_id": 120}),
            ("m4", {}),
            ("m5", None),
        ]
    )

    ids = await _auto_collect_item_ids(cast(AsyncSession, session), 119)

    assert ids == ["m1", "m2"]
    sql = str(session.executed)
    assert sql.startswith("SELECT media_items.id, media_items.provider_ids")
    assert "streams_json" not in sql
