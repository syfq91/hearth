"""Auto-watched decisions based on progress or chapter markers.

The decision is pure so it can be tested exhaustively; routes only persist the
result. Chapters arrive as the JSON dictionaries stored by the scanner or as
``ProbeChapter`` models.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_CREDITS_TITLE = re.compile(r"\b(end[\s_-]*credits?|credits?|outro)\b", re.IGNORECASE)


@dataclass(frozen=True)
class WatchDecision:
    """The clamped position and whether it counts as watched."""

    position_ms: int
    watched: bool
    reason: str | None = None


def _chapter_value(chapter: Any, key: str) -> Any:
    if isinstance(chapter, Mapping):
        return chapter.get(key)
    return getattr(chapter, key, None)


def credits_start_ms(chapters: Sequence[Any]) -> int | None:
    """Return the start of the earliest credits chapter, if one is labelled."""
    starts: list[int] = []
    for chapter in chapters:
        title = _chapter_value(chapter, "title")
        start = _chapter_value(chapter, "start_seconds")
        if not isinstance(title, str) or not _CREDITS_TITLE.search(title):
            continue
        if isinstance(start, bool) or not isinstance(start, (int, float)) or start < 0:
            continue
        starts.append(int(start * 1000))
    return min(starts) if starts else None


def chapters_from_streams(streams_json: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Extract the stored chapter dictionaries from a file's probe payload."""
    if not streams_json:
        return []
    chapters = streams_json.get("chapters")
    if not isinstance(chapters, list):
        return []
    return [chapter for chapter in chapters if isinstance(chapter, dict)]


def resolve_watch_state(
    *,
    position_ms: int,
    duration_ms: int | None,
    chapters: Sequence[Any] = (),
    threshold: float = 0.90,
) -> WatchDecision:
    """Decide whether a position counts as watched.

    Watched when the position reaches a labelled credits chapter, or when it
    passes ``threshold`` of a known duration. A position of zero is never
    watched, and positions are clamped to the duration.
    """
    position = max(0, int(position_ms))
    duration = duration_ms if duration_ms is not None and duration_ms > 0 else None
    if duration is not None:
        position = min(position, duration)

    if position <= 0:
        return WatchDecision(position_ms=position, watched=False)

    credits = credits_start_ms(chapters)
    if credits is not None and position >= credits:
        return WatchDecision(position_ms=position, watched=True, reason="credits")

    if duration is not None and position / duration >= threshold:
        return WatchDecision(position_ms=position, watched=True, reason="threshold")

    return WatchDecision(position_ms=position, watched=False)
