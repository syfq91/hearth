"""TV show domain logic: Next-Up calculation and hierarchy helpers."""

from datetime import datetime

from app.db.models import Episode, UserItemState


def select_next_up_episode(
    episodes: list[Episode],
    watch_states: dict[str, UserItemState],
) -> Episode | None:
    """Select the next episode to watch for a series.

    Rules:
    - Filters to episodes with valid season and episode numbers.
    - Excludes specials (season 0) if any regular seasons (season > 0) exist.
    - If no episodes are watched, returns the first episode in order.
    - If episodes are watched, identifies the last-watched episode (by most recent
      ``last_played_at`` or ``updated_at``, breaking ties with season/episode number).
    - Looks for the first unwatched episode after that last-watched episode in series order
      (handling season boundaries and out-of-order watching).
    - If all episodes after the last-watched episode are watched, falls back to the earliest
      unwatched episode in the series (e.g. skipped episodes).
    - Returns ``None`` if all episodes are watched.
    """
    valid = [
        ep for ep in episodes if ep.season_number is not None and ep.episode_number is not None
    ]
    if not valid:
        return None

    regular = [ep for ep in valid if ep.season_number is not None and ep.season_number > 0]
    candidates = regular if regular else valid
    candidates.sort(key=lambda ep: (ep.season_number or 0, ep.episode_number or 0))

    played_items: list[tuple[Episode, UserItemState]] = []
    for ep in candidates:
        state = watch_states.get(ep.item_id)
        if state is not None and state.played:
            played_items.append((ep, state))

    if not played_items:
        return candidates[0]

    def _sort_key(item: tuple[Episode, UserItemState]) -> tuple[datetime, int, int]:
        ep, state = item
        t = state.last_played_at or state.updated_at
        return (t, ep.season_number or 0, ep.episode_number or 0)

    last_ep, _ = max(played_items, key=_sort_key)
    last_season = last_ep.season_number or 0
    last_episode_num = last_ep.episode_number or 0

    after_episodes = [
        ep
        for ep in candidates
        if (
            (ep.season_number or 0) > last_season
            or (
                (ep.season_number or 0) == last_season
                and (ep.episode_number or 0) > last_episode_num
            )
        )
    ]

    for ep in after_episodes:
        st = watch_states.get(ep.item_id)
        if st is None or not st.played:
            return ep

    # Fallback for out-of-order watching where user watched ahead to the end
    for ep in candidates:
        st = watch_states.get(ep.item_id)
        if st is None or not st.played:
            return ep

    return None
