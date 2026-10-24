"""Unit tests for the auto-watched decision."""

import pytest

from app.services.playback_state import credits_start_ms, resolve_watch_state


@pytest.mark.parametrize(
    ("position_ms", "watched", "reason"),
    [
        (0, False, None),
        (5400_000, True, "threshold"),  # exactly 90%
        (5399_999, False, None),
        (9000_000, True, "threshold"),
    ],
)
def test_threshold_marks_watched_at_ninety_percent(
    position_ms: int, watched: bool, reason: str | None
) -> None:
    result = resolve_watch_state(
        position_ms=position_ms,
        duration_ms=6000_000,
    )
    assert (result.watched, result.reason) == (watched, reason)


def test_unknown_duration_never_auto_marks() -> None:
    result = resolve_watch_state(position_ms=5000_000, duration_ms=None)
    assert result.watched is False


def test_position_is_clamped_to_the_duration() -> None:
    result = resolve_watch_state(position_ms=9000_000, duration_ms=6000_000)
    assert result.watched is True
    assert result.position_ms == 6000_000


def test_credits_chapter_marks_watched_below_the_threshold() -> None:
    chapters = [
        {"start_seconds": 0.0, "title": "Opening"},
        {"start_seconds": 5800.0, "title": "End Credits"},
    ]
    result = resolve_watch_state(position_ms=5800_000, duration_ms=6000_000, chapters=chapters)
    assert (result.watched, result.reason) == (True, "credits")


def test_credits_chapter_before_reaching_it_is_not_watched() -> None:
    chapters = [{"start_seconds": 5800.0, "title": "End Credits"}]
    result = resolve_watch_state(position_ms=5000_000, duration_ms=6000_000, chapters=chapters)
    assert result.watched is False


@pytest.mark.parametrize(
    "title", ["Credits", "End Credits", "end credits", "Outro", "Closing Credits"]
)
def test_credits_titles_are_recognized(title: str) -> None:
    assert credits_start_ms([{"start_seconds": 42.0, "title": title}]) == 42_000


@pytest.mark.parametrize("title", ["Opening", "Intro", "Main Title", None])
def test_non_credits_titles_are_ignored(title: str | None) -> None:
    assert credits_start_ms([{"start_seconds": 42.0, "title": title}]) is None


def test_earliest_credits_chapter_wins() -> None:
    chapters = [
        {"start_seconds": 100.0, "title": "End Credits"},
        {"start_seconds": 50.0, "title": "Credits"},
    ]
    assert credits_start_ms(chapters) == 50_000


def test_credits_at_zero_does_not_mark_an_unstarted_item() -> None:
    result = resolve_watch_state(
        position_ms=0,
        duration_ms=6000_000,
        chapters=[{"start_seconds": 0.0, "title": "Credits"}],
    )
    assert result.watched is False


def test_malformed_chapter_entries_are_ignored() -> None:
    chapters = [{"start_seconds": "nope", "title": "Credits"}, {"title": "End Credits"}]
    assert credits_start_ms(chapters) is None
    assert (
        resolve_watch_state(position_ms=1000, duration_ms=6000_000, chapters=chapters).watched
        is False
    )
