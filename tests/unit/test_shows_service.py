"""Unit tests for TV shows service and next-up logic."""

from datetime import UTC, datetime, timedelta

from app.db.models import Episode, UserItemState
from app.services.shows import select_next_up_episode

NOW = datetime.now(UTC)


def _ep(item_id: str, season: int, episode: int, series_id: str = "series-1") -> Episode:
    return Episode(
        item_id=item_id,
        series_id=series_id,
        season_number=season,
        episode_number=episode,
    )


def _state(item_id: str, played: bool = True, played_at: datetime | None = None) -> UserItemState:
    return UserItemState(
        user_id="user-1",
        item_id=item_id,
        played=played,
        last_played_at=played_at or NOW,
    )


def test_next_up_unwatched_series() -> None:
    episodes = [
        _ep("ep-1", 1, 1),
        _ep("ep-2", 1, 2),
    ]
    states: dict[str, UserItemState] = {}
    next_up = select_next_up_episode(episodes, states)
    assert next_up is not None
    assert next_up.item_id == "ep-1"


def test_next_up_normal_progression() -> None:
    episodes = [
        _ep("ep-1", 1, 1),
        _ep("ep-2", 1, 2),
        _ep("ep-3", 1, 3),
    ]
    states = {
        "ep-1": _state("ep-1", played=True, played_at=NOW - timedelta(hours=1)),
    }
    next_up = select_next_up_episode(episodes, states)
    assert next_up is not None
    assert next_up.item_id == "ep-2"


def test_next_up_across_season_boundary() -> None:
    episodes = [
        _ep("ep-s1e1", 1, 1),
        _ep("ep-s1e2", 1, 2),
        _ep("ep-s2e1", 2, 1),
        _ep("ep-s2e2", 2, 2),
    ]
    states = {
        "ep-s1e1": _state("ep-s1e1", played=True, played_at=NOW - timedelta(hours=2)),
        "ep-s1e2": _state("ep-s1e2", played=True, played_at=NOW - timedelta(hours=1)),
    }
    next_up = select_next_up_episode(episodes, states)
    assert next_up is not None
    assert next_up.item_id == "ep-s2e1"


def test_next_up_out_of_order_watched_ahead() -> None:
    # User watched E1, then jumped ahead to E3 (leaving E2 unwatched)
    episodes = [
        _ep("ep-1", 1, 1),
        _ep("ep-2", 1, 2),
        _ep("ep-3", 1, 3),
        _ep("ep-4", 1, 4),
    ]
    states = {
        "ep-1": _state("ep-1", played=True, played_at=NOW - timedelta(hours=2)),
        "ep-3": _state("ep-3", played=True, played_at=NOW - timedelta(hours=1)),
    }
    # Last watched is ep-3. First unwatched after last watched is ep-4.
    next_up = select_next_up_episode(episodes, states)
    assert next_up is not None
    assert next_up.item_id == "ep-4"


def test_next_up_out_of_order_finale_watched_earlier_skipped() -> None:
    # User watched E1, E3, E4 (leaving E2 skipped). E4 is finale.
    episodes = [
        _ep("ep-1", 1, 1),
        _ep("ep-2", 1, 2),
        _ep("ep-3", 1, 3),
        _ep("ep-4", 1, 4),
    ]
    states = {
        "ep-1": _state("ep-1", played=True, played_at=NOW - timedelta(hours=3)),
        "ep-3": _state("ep-3", played=True, played_at=NOW - timedelta(hours=2)),
        "ep-4": _state("ep-4", played=True, played_at=NOW - timedelta(hours=1)),
    }
    # No unwatched after E4, so falls back to earliest unwatched: ep-2.
    next_up = select_next_up_episode(episodes, states)
    assert next_up is not None
    assert next_up.item_id == "ep-2"


def test_next_up_out_of_order_went_back_to_earlier_episode() -> None:
    # User watched E3 first, then went back and watched E1.
    # Last watched is now E1 (more recent played_at).
    episodes = [
        _ep("ep-1", 1, 1),
        _ep("ep-2", 1, 2),
        _ep("ep-3", 1, 3),
        _ep("ep-4", 1, 4),
    ]
    states = {
        "ep-3": _state("ep-3", played=True, played_at=NOW - timedelta(hours=2)),
        "ep-1": _state("ep-1", played=True, played_at=NOW - timedelta(hours=1)),
    }
    # Last watched is ep-1. First unwatched after ep-1 is ep-2 (since ep-3 is watched).
    next_up = select_next_up_episode(episodes, states)
    assert next_up is not None
    assert next_up.item_id == "ep-2"


def test_next_up_all_episodes_watched() -> None:
    episodes = [
        _ep("ep-1", 1, 1),
        _ep("ep-2", 1, 2),
    ]
    states = {
        "ep-1": _state("ep-1", played=True, played_at=NOW - timedelta(hours=2)),
        "ep-2": _state("ep-2", played=True, played_at=NOW - timedelta(hours=1)),
    }
    next_up = select_next_up_episode(episodes, states)
    assert next_up is None


def test_next_up_skips_specials_when_regular_seasons_exist() -> None:
    episodes = [
        _ep("ep-s0e1", 0, 1),
        _ep("ep-s1e1", 1, 1),
        _ep("ep-s1e2", 1, 2),
    ]
    states: dict[str, UserItemState] = {}
    # Should start on Season 1, not Season 0
    next_up = select_next_up_episode(episodes, states)
    assert next_up is not None
    assert next_up.item_id == "ep-s1e1"


def test_next_up_uses_specials_when_only_specials_exist() -> None:
    episodes = [
        _ep("ep-s0e1", 0, 1),
        _ep("ep-s0e2", 0, 2),
    ]
    states: dict[str, UserItemState] = {}
    next_up = select_next_up_episode(episodes, states)
    assert next_up is not None
    assert next_up.item_id == "ep-s0e1"
