"""Playback progress, watched flags, favorites and ratings for the current user.

The auto-watched decision lives in :mod:`app.services.playback_state`; these
routes only load the item, enforce library access and persist the result.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.items import (
    SubtitleResponse,
    WatchStateResponse,
    subtitle_responses,
    watch_state_response,
)
from app.core.config import Settings
from app.core.deps import (
    KeysDep,
    RequireUser,
    SettingsDep,
    user_can_access_library,
)
from app.core.errors import NotFoundError
from app.core.tokens import create_media_token
from app.db.base import utcnow
from app.db.models import (
    Episode,
    LibraryPath,
    MediaFile,
    MediaItem,
    Season,
    Track,
    User,
    UserItemState,
)
from app.db.session import SessionDep
from app.services.events import get_broadcaster
from app.services.libraries import path_is_within_roots
from app.services.media.probe import ProbeResult, ProbeStream
from app.services.playback_state import chapters_from_streams, resolve_watch_state
from app.services.ratings import is_rating_permitted
from app.services.scrobbling.listenbrainz import enqueue_scrobble
from app.services.transcode.capabilities import probe_transcode_capabilities
from app.services.transcode.decision import (
    PlaybackOptions,
    TranscodePlan,
    UserLimits,
    evaluate_playback_plan,
)
from app.services.transcode.profile import ClientCapabilities
from app.services.transcode.session import get_session_manager

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


PlaybackMode = Literal["direct_play", "remux_hls", "transcode_hls"]


class PlaybackInfoRequest(BaseModel):
    """A playback-info request; omitting it asks for defaults."""

    file_id: str | None = Field(default=None, max_length=36)
    capabilities: ClientCapabilities = Field(default_factory=ClientCapabilities)
    audio_track_index: int | None = None
    subtitle_track_index: int | None = None
    start_time_offset_seconds: float = 0.0
    target_max_bitrate: int | None = Field(default=None, gt=0)


class PlaybackFileResponse(BaseModel):
    """The selected playable file."""

    id: str
    container: str | None = None
    duration_seconds: float | None = None
    size_bytes: int


class PlaybackInfoResponse(BaseModel):
    """A playback decision plus URLs the client can use directly.

    ``url`` is set for direct play. ``hls_url`` and ``session_id`` are populated
    for the HLS modes so clients can stream fMP4 live transcoding.
    """

    mode: PlaybackMode
    url: str | None = None
    hls_url: str | None = None
    session_id: str | None = None
    file: PlaybackFileResponse
    subtitles: list[SubtitleResponse] = Field(default_factory=list)
    audio_streams: list[ProbeStream] = Field(default_factory=list)
    embedded_subtitles: list[ProbeStream] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    plan: TranscodePlan | None = None
    command: list[str] | None = None


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

    if (
        item.type == "track"
        and decision.watched
        and not was_played
        and settings.listenbrainz_enabled
    ):
        await enqueue_scrobble(session, user.id, item.id)

    if item.type == "track" and item.track and item.track.album_id:
        alb_state = await _load_state(session, user.id, item.track.album_id)
        if body.state is not None or decision.position_ms > 0:
            alb_state.last_played_at = now
        if decision.watched and not was_played:
            track_ids = list(
                await session.scalars(
                    select(Track.item_id).where(Track.album_id == item.track.album_id)
                )
            )
            if track_ids:
                played_count = (
                    await session.scalar(
                        select(func.count(UserItemState.item_id)).where(
                            UserItemState.user_id == user.id,
                            UserItemState.item_id.in_(track_ids),
                            UserItemState.played.is_(True),
                        )
                    )
                    or 0
                )
                if played_count + 1 >= len(track_ids):
                    alb_state.played = True
                    alb_state.play_count += 1

    await _persist(session, state)

    if body.state in {"playing", "paused", "stopped"}:
        event_name = "playback.active" if body.state == "playing" else f"playback.{body.state}"
        get_broadcaster().publish(
            event_name,
            {
                "user_id": user.id,
                "item_id": item.id,
                "library_id": item.library_id,
                "position_ms": state.position_ms,
                "duration_ms": body.duration_ms or _duration_ms(item),
                "title": item.title,
                "type": item.type,
                "state": body.state,
            },
        )

    return ProgressResponse(
        **watch_state_response(state).model_dump(),
        auto_watched=decision.watched and not was_played,
        watched_reason=decision.reason,
    )


@router.post("/items/{item_id}/playback-info")
async def playback_info(
    item_id: str,
    user: RequireUser,
    settings: SettingsDep,
    keys: KeysDep,
    session: SessionDep,
    body: PlaybackInfoRequest | None = None,
) -> PlaybackInfoResponse:
    """Return how the client should play an item.

    Evaluates client capabilities against media streams, user bitrate/transcode
    limits, and server hardware capabilities. Returns either direct play or an
    HLS stream session.
    """
    request = body or PlaybackInfoRequest()
    item = await _load_item(session, user, item_id)
    media_file = _select_file(item, request.file_id)
    await _require_inside_library(session, item, media_file)

    token, expires_at = create_media_token(
        user_id=user.id,
        token_version=user.token_version,
        settings=settings,
        key=keys.media,
        item_id=item.id,
    )
    encoded_token = quote(token, safe="")

    # Parse stored streams
    probe_res: ProbeResult
    try:
        if isinstance(media_file.streams_json, dict) and "container" in media_file.streams_json:
            probe_res = ProbeResult.model_validate(media_file.streams_json)
        elif isinstance(media_file.streams_json, str):
            probe_res = ProbeResult.model_validate_json(media_file.streams_json)
        else:
            probe_res = ProbeResult(
                container=media_file.container or "mp4",
                duration_seconds=media_file.duration_seconds,
                size_bytes=media_file.size_bytes,
            )
    except Exception:
        probe_res = ProbeResult(
            container=media_file.container or "mp4",
            duration_seconds=media_file.duration_seconds,
            size_bytes=media_file.size_bytes,
        )

    # Evaluate decision engine
    server_caps = await probe_transcode_capabilities(settings)
    user_limits = UserLimits(
        max_bitrate=user.max_bitrate,
        allow_transcode=user.allow_transcode,
    )
    playback_options = PlaybackOptions(
        audio_track_index=request.audio_track_index,
        subtitle_track_index=request.subtitle_track_index,
        target_max_bitrate=request.target_max_bitrate,
    )
    plan = evaluate_playback_plan(
        probe_res,
        request.capabilities,
        user_limits,
        server_caps,
        playback_options,
    )

    file_resp = PlaybackFileResponse(
        id=media_file.id,
        container=media_file.container,
        duration_seconds=media_file.duration_seconds,
        size_bytes=media_file.size_bytes,
    )
    subtitles = subtitle_responses(media_file.id, media_file.path, token=token)
    audio_streams = [s for s in probe_res.streams if s.codec_type == "audio"]
    embedded_subtitles = [s for s in probe_res.streams if s.codec_type == "subtitle"]

    if plan.mode == "direct_play":
        return PlaybackInfoResponse(
            mode="direct_play",
            url=f"/api/v1/stream/{media_file.id}?token={encoded_token}",
            file=file_resp,
            subtitles=subtitles,
            audio_streams=audio_streams,
            embedded_subtitles=embedded_subtitles,
            reasons=plan.reasons,
            expires_at=expires_at,
            plan=plan,
        )

    # Launch or get HLS transcoding session
    manager = get_session_manager(settings)
    transcode_session = await manager.create_session(
        user_id=user.id,
        item_id=item.id,
        file_id=media_file.id,
        input_path=Path(media_file.path),
        plan=plan,
        start_offset=request.start_time_offset_seconds,
    )

    return PlaybackInfoResponse(
        mode=plan.mode,
        hls_url=f"/api/v1/hls/{transcode_session.id}/master.m3u8?token={encoded_token}",
        session_id=transcode_session.id,
        file=file_resp,
        subtitles=subtitles if plan.subtitle_action == "sidecar_vtt" else [],
        audio_streams=audio_streams,
        embedded_subtitles=embedded_subtitles,
        reasons=plan.reasons,
        expires_at=expires_at,
        plan=plan,
        command=transcode_session.cmd,
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
    now = utcnow()
    if item.type == "season":
        episodes = list(await session.scalars(select(Episode).where(Episode.season_id == item.id)))
        for ep in episodes:
            ep_state = await _load_state(session, user.id, ep.item_id)
            ep_state.played = body.watched
            if body.watched:
                ep_state.position_ms = 0
                ep_state.last_played_at = now
                ep_state.play_count = max(ep_state.play_count, 1)
            else:
                ep_state.position_ms = 0
    elif item.type == "series":
        episodes = list(await session.scalars(select(Episode).where(Episode.series_id == item.id)))
        for ep in episodes:
            ep_state = await _load_state(session, user.id, ep.item_id)
            ep_state.played = body.watched
            if body.watched:
                ep_state.position_ms = 0
                ep_state.last_played_at = now
                ep_state.play_count = max(ep_state.play_count, 1)
            else:
                ep_state.position_ms = 0
        seasons = list(await session.scalars(select(Season).where(Season.series_id == item.id)))
        for s in seasons:
            s_state = await _load_state(session, user.id, s.item_id)
            s_state.played = body.watched
            if body.watched:
                s_state.position_ms = 0
                s_state.last_played_at = now
                s_state.play_count = max(s_state.play_count, 1)
            else:
                s_state.position_ms = 0
    elif item.type == "album":
        tracks = list(await session.scalars(select(Track).where(Track.album_id == item.id)))
        for tr in tracks:
            tr_state = await _load_state(session, user.id, tr.item_id)
            tr_state.played = body.watched
            if body.watched:
                tr_state.position_ms = 0
                tr_state.last_played_at = now
                tr_state.play_count = max(tr_state.play_count, 1)
            else:
                tr_state.position_ms = 0

    state = await _load_state(session, user.id, item.id)
    state.played = body.watched
    if not body.watched:
        state.position_ms = 0
    elif body.watched:
        state.last_played_at = now
        state.play_count = max(state.play_count, 1)
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
    if (
        item is None
        or not await user_can_access_library(session, user, item.library_id)
        or not is_rating_permitted(item.content_rating, user.max_rating)
    ):
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


def _select_file(item: MediaItem, file_id: str | None) -> MediaFile:
    """Pick the requested file, or the first playable version."""
    candidates = [file for file in item.files if file.missing_since is None]
    if (
        not candidates
        and item.episode
        and item.episode.file
        and item.episode.file.missing_since is None
    ):
        candidates = [item.episode.file]
    if not candidates and item.track and item.track.file and item.track.file.missing_since is None:
        candidates = [item.track.file]
    if file_id is not None:
        candidates = [file for file in candidates if file.id == file_id]
    if not candidates:
        raise NotFoundError("Media file not found", code="file_not_found")
    return sorted(candidates, key=lambda file: file.path)[0]


async def _require_inside_library(
    session: AsyncSession, item: MediaItem, media_file: MediaFile
) -> None:
    """Reject a file row that no longer resolves inside the library roots."""
    roots = list(
        await session.scalars(
            select(LibraryPath.path).where(LibraryPath.library_id == item.library_id)
        )
    )
    if not path_is_within_roots(Path(media_file.path), roots):
        raise NotFoundError("Media file not found", code="file_not_found")


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
    if not durations and item.episode and item.episode.file and item.episode.file.duration_seconds:
        durations = [item.episode.file.duration_seconds]
    if not durations and item.track and item.track.duration_seconds:
        durations = [item.track.duration_seconds]
    return int(max(durations) * 1000) if durations else None


def _chapters(item: MediaItem) -> list[dict[str, Any]]:
    """Return chapters from the longest probed file that has any."""
    files = sorted(item.files, key=lambda file: file.duration_seconds or 0, reverse=True)
    if not files and item.episode and item.episode.file:
        files = [item.episode.file]
    for media_file in files:
        chapters = chapters_from_streams(media_file.streams_json)
        if chapters:
            return chapters
    return []


__all__ = ["router"]
