"""TV series, season, episode, next-up, and watch-state endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.v1.items import (
    ImageResponse,
    ItemSummary,
    MediaFileResponse,
    PersonCreditResponse,
    WatchStateResponse,
    _image_response,
    _ordered_images,
    _people,
    _primary_image,
    item_summary,
    subtitle_responses,
    watch_state_response,
    watch_states,
)
from app.api.v1.playback import WatchedRequest, _load_state
from app.core.deps import RequireUser, require_library_access, user_can_access_library
from app.core.errors import NotFoundError
from app.db.base import utcnow
from app.db.models import (
    Episode,
    MediaItem,
    Season,
    Series,
    UserItemState,
)
from app.db.session import SessionDep
from app.services.shows import select_next_up_episode

router = APIRouter(tags=["shows"])


class SeasonSummary(BaseModel):
    """Catalog card for a season."""

    id: str
    series_id: str
    season_number: int
    title: str
    overview: str | None = None
    episode_count: int = 0
    air_date: str | None = None
    poster: ImageResponse | None = None
    watch_state: WatchStateResponse


class EpisodeSummary(BaseModel):
    """Catalog card for an episode."""

    id: str
    series_id: str
    season_id: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    title: str
    overview: str | None = None
    air_date: str | None = None
    runtime_seconds: int | None = None
    rating: float | None = None
    still: ImageResponse | None = None
    watch_state: WatchStateResponse
    files: list[MediaFileResponse] = []


class SeriesDetail(ItemSummary):
    """Complete detail response for a TV show."""

    original_title: str | None = None
    status: str | None = None
    network: str | None = None
    total_seasons: int = 0
    total_episodes: int = 0
    seasons: list[SeasonSummary] = []
    people: list[PersonCreditResponse] = []
    images: list[ImageResponse] = []
    provider_ids: dict[str, str] = {}


class SeasonDetail(SeasonSummary):
    """Complete detail response for a season including its episodes."""

    episodes: list[EpisodeSummary] = []


class EpisodeDetail(EpisodeSummary):
    """Complete detail response for an episode."""

    series_title: str
    season_title: str | None = None
    people: list[PersonCreditResponse] = []
    images: list[ImageResponse] = []
    provider_ids: dict[str, str] = {}


class NextUpEpisode(EpisodeSummary):
    """An episode queued in Next Up with its parent series information."""

    series_title: str
    series_poster: ImageResponse | None = None
    season_title: str | None = None


class NextUpShelf(BaseModel):
    """A shelf of next-up episodes."""

    items: list[NextUpEpisode]


# ------------------------------------------------------------------- Endpoints


@router.get("/series/{series_id}")
async def get_series_detail(
    series_id: str,
    user: RequireUser,
    session: SessionDep,
) -> Any:
    """Return complete details for a TV series or book series."""
    item = await session.get(MediaItem, series_id)
    if (
        item is None
        or item.type != "series"
        or not await user_can_access_library(session, user, item.library_id)
    ):
        from app.api.v1.books import get_series_detail as get_book_series_detail

        try:
            return await get_book_series_detail(series_id, user, session)
        except NotFoundError:
            raise NotFoundError("Series not found", code="series_not_found") from None

    series = item.series
    series_row = series if series is not None else Series(item_id=item.id)

    # Load seasons
    seasons_query = (
        select(Season).where(Season.series_id == series_id).order_by(Season.season_number.asc())
    )
    seasons = list(await session.scalars(seasons_query))
    season_item_ids = [s.item_id for s in seasons]
    season_states = await watch_states(session, user.id, season_item_ids)

    # Episode counts per season
    ep_counts_query = (
        select(Episode.season_id, func.count(Episode.item_id))
        .where(Episode.series_id == series_id)
        .group_by(Episode.season_id)
    )
    ep_counts = dict((await session.execute(ep_counts_query)).all())

    season_summaries = [
        SeasonSummary(
            id=s.item_id,
            series_id=s.series_id,
            season_number=s.season_number,
            title=s.item.title if s.item else f"Season {s.season_number}",
            overview=s.item.overview if s.item else None,
            episode_count=ep_counts.get(s.item_id, 0),
            air_date=s.air_date,
            poster=_primary_image(s.item, "poster") if s.item else None,
            watch_state=watch_state_response(season_states.get(s.item_id)),
        )
        for s in seasons
    ]

    total_episodes = sum(ep_counts.values())
    people = await _people(session, item.id)
    summary = item_summary(item, await _load_state(session, user.id, item.id))

    return SeriesDetail(
        **summary.model_dump(),
        original_title=series_row.original_title,
        status=series_row.status,
        network=series_row.network,
        total_seasons=len(seasons),
        total_episodes=total_episodes,
        seasons=season_summaries,
        people=people,
        images=[_image_response(image) for image in _ordered_images(item)],
        provider_ids={str(k): str(v) for k, v in (item.provider_ids or {}).items()},
    )


@router.get("/series/{series_id}/seasons")
async def list_series_seasons(
    series_id: str,
    user: RequireUser,
    session: SessionDep,
) -> list[SeasonSummary]:
    """Return all seasons belonging to a series."""
    item = await session.get(MediaItem, series_id)
    if (
        item is None
        or item.type != "series"
        or not await user_can_access_library(session, user, item.library_id)
    ):
        raise NotFoundError("Series not found", code="series_not_found")

    seasons = list(
        await session.scalars(
            select(Season).where(Season.series_id == series_id).order_by(Season.season_number.asc())
        )
    )
    season_states = await watch_states(session, user.id, [s.item_id for s in seasons])

    ep_counts_query = (
        select(Episode.season_id, func.count(Episode.item_id))
        .where(Episode.series_id == series_id)
        .group_by(Episode.season_id)
    )
    ep_counts = dict((await session.execute(ep_counts_query)).all())

    return [
        SeasonSummary(
            id=s.item_id,
            series_id=s.series_id,
            season_number=s.season_number,
            title=s.item.title if s.item else f"Season {s.season_number}",
            overview=s.item.overview if s.item else None,
            episode_count=ep_counts.get(s.item_id, 0),
            air_date=s.air_date,
            poster=_primary_image(s.item, "poster") if s.item else None,
            watch_state=watch_state_response(season_states.get(s.item_id)),
        )
        for s in seasons
    ]


@router.get("/seasons/{season_id}")
async def get_season_detail(
    season_id: str,
    user: RequireUser,
    session: SessionDep,
) -> SeasonDetail:
    """Return season details along with its constituent episodes."""
    item = await session.get(MediaItem, season_id)
    if (
        item is None
        or item.type != "season"
        or not await user_can_access_library(session, user, item.library_id)
    ):
        raise NotFoundError("Season not found", code="season_not_found")

    season = item.season
    if season is None:
        raise NotFoundError("Season not found", code="season_not_found")

    episodes = list(
        await session.scalars(
            select(Episode)
            .where(Episode.season_id == season_id)
            .order_by(Episode.episode_number.asc())
        )
    )
    ep_states = await watch_states(session, user.id, [ep.item_id for ep in episodes])
    season_state = await _load_state(session, user.id, season_id)

    episode_summaries = [_build_episode_summary(ep, ep_states.get(ep.item_id)) for ep in episodes]

    return SeasonDetail(
        id=season.item_id,
        series_id=season.series_id,
        season_number=season.season_number,
        title=item.title,
        overview=item.overview,
        episode_count=len(episodes),
        air_date=season.air_date,
        poster=_primary_image(item, "poster"),
        watch_state=watch_state_response(season_state),
        episodes=episode_summaries,
    )


@router.get("/seasons/{season_id}/episodes")
async def list_season_episodes(
    season_id: str,
    user: RequireUser,
    session: SessionDep,
) -> list[EpisodeSummary]:
    """Return all episodes belonging to a season."""
    item = await session.get(MediaItem, season_id)
    if (
        item is None
        or item.type != "season"
        or not await user_can_access_library(session, user, item.library_id)
    ):
        raise NotFoundError("Season not found", code="season_not_found")

    episodes = list(
        await session.scalars(
            select(Episode)
            .where(Episode.season_id == season_id)
            .order_by(Episode.episode_number.asc())
        )
    )
    ep_states = await watch_states(session, user.id, [ep.item_id for ep in episodes])
    return [_build_episode_summary(ep, ep_states.get(ep.item_id)) for ep in episodes]


@router.get("/episodes/{episode_id}")
async def get_episode_detail(
    episode_id: str,
    user: RequireUser,
    session: SessionDep,
) -> EpisodeDetail:
    """Return complete details for a single episode."""
    item = await session.get(MediaItem, episode_id)
    if (
        item is None
        or item.type != "episode"
        or not await user_can_access_library(session, user, item.library_id)
    ):
        raise NotFoundError("Episode not found", code="episode_not_found")

    episode = item.episode
    if episode is None:
        raise NotFoundError("Episode not found", code="episode_not_found")

    state = await _load_state(session, user.id, item.id)
    summary = _build_episode_summary(episode, state)

    series_title = (
        await session.scalar(select(MediaItem.title).where(MediaItem.id == episode.series_id)) or ""
    )
    season_title = (
        await session.scalar(select(MediaItem.title).where(MediaItem.id == episode.season_id))
        if episode.season_id
        else (f"Season {episode.season_number}" if episode.season_number is not None else None)
    )

    people = await _people(session, item.id)
    return EpisodeDetail(
        **summary.model_dump(),
        series_title=series_title,
        season_title=season_title,
        people=people,
        images=[_image_response(image) for image in _ordered_images(item)],
        provider_ids={str(k): str(v) for k, v in (item.provider_ids or {}).items()},
    )


@router.get("/series/{series_id}/next-up")
async def get_series_next_up(
    series_id: str,
    user: RequireUser,
    session: SessionDep,
) -> NextUpEpisode:
    """Return the next-up episode for a specific series."""
    item = await session.get(MediaItem, series_id)
    if (
        item is None
        or item.type != "series"
        or not await user_can_access_library(session, user, item.library_id)
    ):
        raise NotFoundError("Series not found", code="series_not_found")

    episodes = list(
        await session.scalars(
            select(Episode)
            .where(Episode.series_id == series_id)
            .order_by(Episode.season_number.asc(), Episode.episode_number.asc())
        )
    )
    if not episodes:
        raise NotFoundError("No episodes found for series", code="next_up_not_found")

    states = await watch_states(session, user.id, [ep.item_id for ep in episodes])
    next_ep = select_next_up_episode(episodes, states)
    if next_ep is None:
        raise NotFoundError("All episodes watched", code="next_up_not_found")

    summary = _build_episode_summary(next_ep, states.get(next_ep.item_id))
    series_title = item.title
    series_poster = _primary_image(item, "poster")
    season_title = (
        await session.scalar(select(MediaItem.title).where(MediaItem.id == next_ep.season_id))
        if next_ep.season_id
        else (f"Season {next_ep.season_number}" if next_ep.season_number is not None else None)
    )

    return NextUpEpisode(
        **summary.model_dump(),
        series_title=series_title,
        series_poster=series_poster,
        season_title=season_title,
    )


@router.get("/shows/next-up")
async def list_next_up(
    user: RequireUser,
    session: SessionDep,
    library_id: Annotated[str | None, Query(max_length=36)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> NextUpShelf:
    """Return next-up episodes across active series for the current user."""
    if library_id is not None:
        await require_library_access(library_id, user, session)

    # Find series where user has watched or in-progress activity on episodes
    query = (
        select(Episode.series_id, func.max(UserItemState.last_played_at).label("last_active"))
        .join(UserItemState, UserItemState.item_id == Episode.item_id)
        .where(
            UserItemState.user_id == user.id,
            (UserItemState.played.is_(True) | (UserItemState.position_ms > 0)),
        )
        .group_by(Episode.series_id)
        .order_by(func.max(UserItemState.last_played_at).desc())
    )

    active_series_rows = (await session.execute(query)).all()
    next_up_items: list[NextUpEpisode] = []

    for series_id, _ in active_series_rows:
        if len(next_up_items) >= limit:
            break

        series_item = await session.get(MediaItem, series_id)
        if series_item is None or series_item.type != "series":
            continue
        if library_id is not None and series_item.library_id != library_id:
            continue
        if not await user_can_access_library(session, user, series_item.library_id):
            continue

        episodes = list(
            await session.scalars(
                select(Episode)
                .where(Episode.series_id == series_id)
                .order_by(Episode.season_number.asc(), Episode.episode_number.asc())
            )
        )
        if not episodes:
            continue

        states = await watch_states(session, user.id, [ep.item_id for ep in episodes])
        next_ep = select_next_up_episode(episodes, states)
        if next_ep is None:
            continue

        summary = _build_episode_summary(next_ep, states.get(next_ep.item_id))
        series_title = series_item.title
        series_poster = _primary_image(series_item, "poster")
        season_title = (
            await session.scalar(select(MediaItem.title).where(MediaItem.id == next_ep.season_id))
            if next_ep.season_id
            else (f"Season {next_ep.season_number}" if next_ep.season_number is not None else None)
        )
        next_up_items.append(
            NextUpEpisode(
                **summary.model_dump(),
                series_title=series_title,
                series_poster=series_poster,
                season_title=season_title,
            )
        )

    return NextUpShelf(items=next_up_items)


@router.post("/seasons/{season_id}/watched")
async def mark_season_watched(
    season_id: str,
    user: RequireUser,
    session: SessionDep,
    body: WatchedRequest,
) -> WatchStateResponse:
    """Mark all episodes in a season as watched or unwatched."""
    season_item = await session.get(MediaItem, season_id)
    if (
        season_item is None
        or season_item.type != "season"
        or not await user_can_access_library(session, user, season_item.library_id)
    ):
        raise NotFoundError("Season not found", code="season_not_found")

    now = utcnow()
    episodes = list(await session.scalars(select(Episode).where(Episode.season_id == season_id)))
    for ep in episodes:
        state = await _load_state(session, user.id, ep.item_id)
        state.played = body.watched
        if body.watched:
            state.position_ms = 0
            state.last_played_at = now
            state.play_count = max(state.play_count, 1)
        else:
            state.position_ms = 0

    season_state = await _load_state(session, user.id, season_id)
    season_state.played = body.watched
    if body.watched:
        season_state.position_ms = 0
        season_state.last_played_at = now
        season_state.play_count = max(season_state.play_count, 1)
    else:
        season_state.position_ms = 0

    # If all episodes of the series are now watched, mark series watched too
    season = season_item.season
    if season is not None:
        series_id = season.series_id
        if body.watched:
            all_eps = list(
                await session.scalars(select(Episode).where(Episode.series_id == series_id))
            )
            all_states = await watch_states(session, user.id, [e.item_id for e in all_eps])
            if all(all_states.get(e.item_id) and all_states[e.item_id].played for e in all_eps):
                series_state = await _load_state(session, user.id, series_id)
                series_state.played = True
                series_state.position_ms = 0
                series_state.last_played_at = now
        else:
            series_state = await _load_state(session, user.id, series_id)
            if series_state.played:
                series_state.played = False

    await session.commit()
    await session.refresh(season_state)
    return watch_state_response(season_state)


@router.post("/series/{series_id}/watched")
async def mark_series_watched(
    series_id: str,
    user: RequireUser,
    session: SessionDep,
    body: WatchedRequest,
) -> WatchStateResponse:
    """Mark all seasons and episodes in a series as watched or unwatched."""
    series_item = await session.get(MediaItem, series_id)
    if (
        series_item is None
        or series_item.type != "series"
        or not await user_can_access_library(session, user, series_item.library_id)
    ):
        raise NotFoundError("Series not found", code="series_not_found")

    now = utcnow()
    episodes = list(await session.scalars(select(Episode).where(Episode.series_id == series_id)))
    for ep in episodes:
        state = await _load_state(session, user.id, ep.item_id)
        state.played = body.watched
        if body.watched:
            state.position_ms = 0
            state.last_played_at = now
            state.play_count = max(state.play_count, 1)
        else:
            state.position_ms = 0

    seasons = list(await session.scalars(select(Season).where(Season.series_id == series_id)))
    for s in seasons:
        season_state = await _load_state(session, user.id, s.item_id)
        season_state.played = body.watched
        if body.watched:
            season_state.position_ms = 0
            season_state.last_played_at = now
            season_state.play_count = max(season_state.play_count, 1)
        else:
            season_state.position_ms = 0

    series_state = await _load_state(session, user.id, series_id)
    series_state.played = body.watched
    if body.watched:
        series_state.position_ms = 0
        series_state.last_played_at = now
        series_state.play_count = max(series_state.play_count, 1)
    else:
        series_state.position_ms = 0

    await session.commit()
    await session.refresh(series_state)
    return watch_state_response(series_state)


# ------------------------------------------------------------------- Helpers


def _build_episode_summary(episode: Episode, state: UserItemState | None) -> EpisodeSummary:
    item = episode.item
    files: list[MediaFileResponse] = []
    if item and item.files:
        files = [
            MediaFileResponse(
                id=media_file.id,
                container=media_file.container,
                duration_seconds=media_file.duration_seconds,
                bit_rate=media_file.bit_rate,
                size_bytes=media_file.size_bytes,
                version_label=media_file.version_label,
                probe_error=media_file.probe_error,
                missing=media_file.missing_since is not None,
                subtitles=subtitle_responses(media_file.id, media_file.path),
            )
            for media_file in sorted(item.files, key=lambda f: f.path)
        ]
    elif episode.file:
        media_file = episode.file
        files = [
            MediaFileResponse(
                id=media_file.id,
                container=media_file.container,
                duration_seconds=media_file.duration_seconds,
                bit_rate=media_file.bit_rate,
                size_bytes=media_file.size_bytes,
                version_label=media_file.version_label,
                probe_error=media_file.probe_error,
                missing=media_file.missing_since is not None,
                subtitles=subtitle_responses(media_file.id, media_file.path),
            )
        ]

    still_img = None
    if item:
        still_img = _primary_image(item, "still") or _primary_image(item, "poster")

    return EpisodeSummary(
        id=episode.item_id,
        series_id=episode.series_id,
        season_id=episode.season_id,
        season_number=episode.season_number,
        episode_number=episode.episode_number,
        title=item.title if item else f"Episode {episode.episode_number}",
        overview=item.overview if item else None,
        air_date=episode.air_date,
        runtime_seconds=item.runtime_seconds if item else None,
        rating=item.rating if item else None,
        still=still_img,
        watch_state=watch_state_response(state),
        files=files,
    )


__all__ = [
    "EpisodeDetail",
    "EpisodeSummary",
    "NextUpEpisode",
    "NextUpShelf",
    "SeasonDetail",
    "SeasonSummary",
    "SeriesDetail",
    "router",
]
