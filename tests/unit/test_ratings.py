"""Unit tests for content ratings classification and filtering."""

from app.services.ratings import (
    allowed_content_ratings,
    is_rating_permitted,
    rating_level,
)


def test_rating_levels() -> None:
    assert rating_level("G") == 1
    assert rating_level("TV-Y") == 1
    assert rating_level("PG") == 2
    assert rating_level("TV-PG") == 2
    assert rating_level("PG-13") == 3
    assert rating_level("TV-14") == 3
    assert rating_level("R") == 4
    assert rating_level("NC-17") == 5
    assert rating_level("TV-MA") == 5
    assert rating_level("UNRATED") == 5
    assert rating_level(None) == 0
    assert rating_level("unknown") == 0


def test_is_rating_permitted() -> None:
    # No user max_rating -> everything allowed
    assert is_rating_permitted("R", None) is True
    assert is_rating_permitted("NC-17", "") is True

    # User max_rating PG-13 (level 3)
    assert is_rating_permitted("G", "PG-13") is True
    assert is_rating_permitted("PG", "PG-13") is True
    assert is_rating_permitted("PG-13", "PG-13") is True
    assert is_rating_permitted("TV-14", "PG-13") is True
    assert is_rating_permitted("R", "PG-13") is False
    assert is_rating_permitted("NC-17", "PG-13") is False
    assert is_rating_permitted("TV-MA", "PG-13") is False

    # User max_rating PG (level 2)
    assert is_rating_permitted("G", "PG") is True
    assert is_rating_permitted("PG", "PG") is True
    assert is_rating_permitted("PG-13", "PG") is False
    assert is_rating_permitted("R", "PG") is False


def test_allowed_content_ratings() -> None:
    assert allowed_content_ratings(None) is None
    assert allowed_content_ratings("") is None

    pg_allowed = allowed_content_ratings("PG")
    assert pg_allowed is not None
    assert "g" in pg_allowed
    assert "G" in pg_allowed
    assert "pg" in pg_allowed
    assert "PG" in pg_allowed
    assert "r" not in pg_allowed
    assert "R" not in pg_allowed
