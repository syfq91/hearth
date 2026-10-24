"""Playback progress, watched flags, favorites and ratings for the current user.

The auto-watched decision lives in :mod:`app.services.playback_state`; these
routes only load the item, enforce library access and persist the result.
"""

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.items import WatchStateResponse, watch_state_response
from app.core.config import Settings
from app.core.deps import RequireUser, SettingsDep, user_can_access_library
from app.core.errors import NotFoundError
from app.db.base import utcnow
from app.db.models import MediaItem, User, UserItemState
from app.db.session import SessionDep
from app.services.playback_state import chapters_from_streams, resolve_watch_state

router = APIRouter(tags=["playback"])

PlayState = Literal["playing", "paused", "stopped"]


class ProgressRequest(BaseModel):
    """A playback checkpoint from the client."""

    position_ms: int = Field(ge=0)
    duration_ms: int | None = Field(default=None, gt=0)
    state: PlayState | None = None


class ProgressResponse(WatchStateResponse):
    """The stored watch state plus whether this report finished the item."""

    auto_watched: bool = False
    watched_reason: str | None = None


class WatchedRequest(BaseModel):
    """Mark an item watched or unwatched."""

    watched: bool = True


class FavoriteRequest(BaseModel):
    """Set or clear the favorite flag."""

    favorite: bool = True


class RatingRequest(BaseModel):
    """Set a personal rating from 1 to 10, or ``null`` to clear it."""

    rating: int | None = Field(default=None, ge=1, le=10)


@router.post("/items/{item_id}/progress")
async def report_progress(
    item_id: str,
    body: ProgressRequest,
    user: RequireUser,
    settings: SettingsDep,
    session: SessionDep,
) -> ProgressResponse:
    """Store a playback position and auto-mark the item watched when finished.

    Watched when the position reaches a labelled credits chapter or
    ``settings.watched_threshold`` of the known duration. A new playback session
    (and therefore one more ``play_count``) starts when the client reports
    ``playing`` after at least ``settings.playback_session_gap_seconds`` without
    any progress report.
    """
    item = await _load_item(session, user, item_id)
    state = await _load_state(session, user.id, item.id)

    now = utcnow()
    if body.state == "playing" and _is_new_session(state, now, settings):
        state.play_count += 1

    was_played = state.played
    decision = resolve_watch_state(
        position_ms=body.position_ms,
        duration_ms=body.duration_ms or _duration_ms(item),
        chapters=_chapters(item),
        threshold=settings.watched_threshold,
    )
    state.position_ms = decision.position_ms
    if decision.watched:
        state.played = True
    if body.state is not None or decision.position_ms > 0:
        state.last_played_at = now

    await _persist(session, state)
    return ProgressResponse(
        **watch_state_response(state).model_dump(),
        auto_watched=decision.watched and not was_played,
        watched_reason=decision.reason,
    )


@router.post("/items/{item_id}/watched")
async def set_watched(
    item_id: str,
    user: RequireUser,
    session: SessionDep,
    body: WatchedRequest,
) -> WatchStateResponse:
    """Mark an item watched or unwatched.

    Marking unwatched also clears the stored position so the item starts from
    the beginning next time.
    """
    item = await _load_item(session, user, item_id)
    state = await _load_state(session, user.id, item.id)
    state.played = body.watched
    if not body.watched:
        state.position_ms = 0
    await _persist(session, state)
    return watch_state_response(state)


@router.post("/items/{item_id}/favorite")
async def set_favorite(
    item_id: str,
    user: RequireUser,
    session: SessionDep,
    body: FavoriteRequest,
) -> WatchStateResponse:
    """Add or remove an item from the current user's favorites."""
    item = await _load_item(session, user, item_id)
    state = await _load_state(session, user.id, item.id)
    state.favorite = body.favorite
    await _persist(session, state)
    return watch_state_response(state)


@router.put("/items/{item_id}/rating")
async def set_rating(
    item_id: str,
    body: RatingRequest,
    user: RequireUser,
    session: SessionDep,
) -> WatchStateResponse:
    """Set or clear the current user's personal rating for an item."""
    item = await _load_item(session, user, item_id)
    state = await _load_state(session, user.id, item.id)
    state.rating = body.rating
    await _persist(session, state)
    return watch_state_response(state)


# ------------------------------------------------------------------- helpers


async def _load_item(session: AsyncSession, user: User, item_id: str) -> MediaItem:
    item = await session.get(MediaItem, item_id)
    if item is None or not await user_can_access_library(session, user, item.library_id):
        raise NotFoundError("Item not found", code="item_not_found")
    return item


async def _load_state(session: AsyncSession, user_id: str, item_id: str) -> UserItemState:
    state = await session.get(UserItemState, (user_id, item_id))
    if state is None:
        state = UserItemState(
            user_id=user_id,
            item_id=item_id,
            played=False,
            position_ms=0,
            favorite=False,
            rating=None,
            play_count=0,
            last_played_at=None,
        )
        session.add(state)
    return state


async def _persist(session: AsyncSession, state: UserItemState) -> None:
    await session.commit()
    await session.refresh(state)


def _is_new_session(state: UserItemState, now: datetime, settings: Settings) -> bool:
    if state.last_played_at is None:
        return True
    idle = (now - state.last_played_at).total_seconds()
    return idle > settings.playback_session_gap_seconds


def _duration_ms(item: MediaItem) -> int | None:
    """Prefer the catalog runtime, then the longest probed file."""
    if item.runtime_seconds:
        return int(item.runtime_seconds * 1000)
    durations = [file.duration_seconds for file in item.files if file.duration_seconds]
    return int(max(durations) * 1000) if durations else None


def _chapters(item: MediaItem) -> list[dict[str, Any]]:
    """Return chapters from the longest probed file that has any."""
    files = sorted(item.files, key=lambda file: file.duration_seconds or 0, reverse=True)
    for media_file in files:
        chapters = chapters_from_streams(media_file.streams_json)
        if chapters:
            return chapters
    return []


__all__ = ["router"]
