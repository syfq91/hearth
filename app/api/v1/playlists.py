"""User-curated playlists across video and audio media items."""

from datetime import datetime

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.api.v1.items import ItemSummary, item_summary, watch_states
from app.api.v1.music import TrackSummary, _track_summary
from app.core.deps import RequireUser
from app.core.errors import NotFoundError, ValidationError
from app.core.ids import new_id
from app.db.base import utcnow
from app.db.models import LibraryAccess, MediaItem, Playlist, PlaylistItem
from app.db.session import SessionDep

router = APIRouter(prefix="/playlists", tags=["playlists"])


class PlaylistSummary(BaseModel):
    """Playlist overview without child items."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str | None = None
    item_count: int = 0
    duration_seconds: int = 0
    total_duration_seconds: float = 0.0
    created_at: datetime
    updated_at: datetime


class PlaylistItemDetail(BaseModel):
    """An item within a playlist."""

    model_config = ConfigDict(extra="forbid")

    id: str
    position: int
    added_at: datetime
    track: TrackSummary | None = None
    item: ItemSummary | None = None


class PlaylistDetail(BaseModel):
    """Playlist detail with ordered items."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str | None = None
    item_count: int = 0
    duration_seconds: int = 0
    total_duration_seconds: float = 0.0
    items: list[PlaylistItemDetail] = []
    created_at: datetime
    updated_at: datetime


class CreatePlaylistRequest(BaseModel):
    """Create a new playlist."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    item_ids: list[str] = Field(default_factory=list)


class UpdatePlaylistRequest(BaseModel):
    """Update playlist metadata."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None


class AddPlaylistItemsRequest(BaseModel):
    """Add items to a playlist."""

    model_config = ConfigDict(extra="ignore")

    item_ids: list[str] = Field(default_factory=list)
    track_ids: list[str] = Field(default_factory=list)


class ReorderPlaylistItemsRequest(BaseModel):
    """Reorder items in a playlist."""

    model_config = ConfigDict(extra="forbid")

    item_ids: list[str] = Field(min_length=1)


@router.get("", response_model=list[PlaylistSummary])
async def list_playlists(
    user: RequireUser,
    session: SessionDep,
) -> list[PlaylistSummary]:
    """List playlists owned by the current user."""
    query = select(Playlist).where(Playlist.user_id == user.id).order_by(Playlist.updated_at.desc())
    playlists = list((await session.scalars(query)).all())
    if not playlists:
        return []

    p_ids = [p.id for p in playlists]
    stats_q = (
        select(
            PlaylistItem.playlist_id,
            func.count(PlaylistItem.id),
            func.coalesce(func.sum(MediaItem.runtime_seconds), 0),
        )
        .join(MediaItem, MediaItem.id == PlaylistItem.item_id)
        .where(PlaylistItem.playlist_id.in_(p_ids))
        .group_by(PlaylistItem.playlist_id)
    )
    stats_res = (await session.execute(stats_q)).all()
    item_stats = {
        row[0]: (int(row[1]), int(row[2] or 0)) for row in stats_res if row[0] is not None
    }

    return [
        PlaylistSummary(
            id=p.id,
            name=p.name,
            description=p.description,
            item_count=item_stats.get(p.id, (0, 0))[0],
            duration_seconds=item_stats.get(p.id, (0, 0))[1],
            total_duration_seconds=float(item_stats.get(p.id, (0, 0))[1]),
            created_at=p.created_at,
            updated_at=p.updated_at,
        )
        for p in playlists
    ]


@router.post("", response_model=PlaylistSummary, status_code=status.HTTP_201_CREATED)
async def create_playlist(
    body: CreatePlaylistRequest,
    user: RequireUser,
    session: SessionDep,
) -> PlaylistSummary:
    """Create a new playlist for the current user."""
    clean_name = body.name.strip()
    if not clean_name:
        raise ValidationError("Playlist name cannot be empty", code="empty_playlist_name")

    now = utcnow()
    playlist = Playlist(
        id=new_id(),
        user_id=user.id,
        name=clean_name,
        description=body.description.strip() if body.description else None,
        created_at=now,
        updated_at=now,
    )
    session.add(playlist)
    await session.flush()

    for pos, iid in enumerate(body.item_ids):
        session.add(
            PlaylistItem(
                id=new_id(),
                playlist_id=playlist.id,
                item_id=iid,
                position=pos,
                added_at=now,
            )
        )

    await session.commit()
    total_dur = 0
    if body.item_ids:
        dur_res = await session.scalar(
            select(func.coalesce(func.sum(MediaItem.runtime_seconds), 0)).where(
                MediaItem.id.in_(body.item_ids)
            )
        )
        total_dur = int(dur_res or 0)

    return PlaylistSummary(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        item_count=len(body.item_ids),
        duration_seconds=total_dur,
        total_duration_seconds=float(total_dur),
        created_at=playlist.created_at,
        updated_at=playlist.updated_at,
    )


@router.get("/{playlist_id}", response_model=PlaylistDetail)
async def get_playlist(
    playlist_id: str,
    user: RequireUser,
    session: SessionDep,
) -> PlaylistDetail:
    """Get playlist detail with items."""
    return await _get_playlist_detail(session, user, playlist_id)


@router.patch("/{playlist_id}", response_model=PlaylistSummary)
async def update_playlist(
    playlist_id: str,
    body: UpdatePlaylistRequest,
    user: RequireUser,
    session: SessionDep,
) -> PlaylistSummary:
    """Update playlist title or description."""
    playlist = await session.get(Playlist, playlist_id)
    if playlist is None or (user.role != "admin" and playlist.user_id != user.id):
        raise NotFoundError("Playlist not found", code="playlist_not_found")

    if body.name is not None:
        clean = body.name.strip()
        if not clean:
            raise ValidationError("Playlist name cannot be empty", code="empty_playlist_name")
        playlist.name = clean
    if body.description is not None:
        playlist.description = body.description.strip() or None
    playlist.updated_at = utcnow()

    await session.commit()

    stats_q = (
        select(
            func.count(PlaylistItem.id),
            func.coalesce(func.sum(MediaItem.runtime_seconds), 0),
        )
        .join(MediaItem, MediaItem.id == PlaylistItem.item_id)
        .where(PlaylistItem.playlist_id == playlist.id)
    )
    row = (await session.execute(stats_q)).first()
    count = int(row[0] or 0) if row else 0
    duration = int(row[1] or 0) if row else 0

    return PlaylistSummary(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        item_count=count,
        duration_seconds=duration,
        total_duration_seconds=float(duration),
        created_at=playlist.created_at,
        updated_at=playlist.updated_at,
    )


@router.delete("/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_playlist(
    playlist_id: str,
    user: RequireUser,
    session: SessionDep,
) -> Response:
    """Delete a playlist."""
    playlist = await session.get(Playlist, playlist_id)
    if playlist is None or (user.role != "admin" and playlist.user_id != user.id):
        raise NotFoundError("Playlist not found", code="playlist_not_found")

    await session.delete(playlist)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{playlist_id}/items", response_model=PlaylistDetail)
async def add_playlist_items(
    playlist_id: str,
    body: AddPlaylistItemsRequest,
    user: RequireUser,
    session: SessionDep,
) -> PlaylistDetail:
    """Add items to a playlist."""
    playlist = await session.get(Playlist, playlist_id)
    if playlist is None or (user.role != "admin" and playlist.user_id != user.id):
        raise NotFoundError("Playlist not found", code="playlist_not_found")

    ids_to_add = [iid for iid in (body.item_ids or body.track_ids) if iid.strip()]
    if not ids_to_add:
        raise ValidationError("No item IDs provided", code="no_item_ids")

    max_pos = (
        await session.scalar(
            select(func.max(PlaylistItem.position)).where(PlaylistItem.playlist_id == playlist.id)
        )
        or -1
    )

    now = utcnow()
    next_pos = max_pos + 1
    for iid in ids_to_add:
        session.add(
            PlaylistItem(
                id=new_id(),
                playlist_id=playlist.id,
                item_id=iid,
                position=next_pos,
                added_at=now,
            )
        )
        next_pos += 1

    playlist.updated_at = now
    await session.commit()
    return await _get_playlist_detail(session, user, playlist.id)


@router.delete("/{playlist_id}/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_playlist_item(
    playlist_id: str,
    item_id: str,
    user: RequireUser,
    session: SessionDep,
) -> Response:
    """Remove an item from a playlist and compact positions."""
    playlist = await session.get(Playlist, playlist_id)
    if playlist is None or (user.role != "admin" and playlist.user_id != user.id):
        raise NotFoundError("Playlist not found", code="playlist_not_found")

    pis = list(
        (
            await session.scalars(
                select(PlaylistItem).where(
                    PlaylistItem.playlist_id == playlist.id,
                    (PlaylistItem.id == item_id) | (PlaylistItem.item_id == item_id),
                )
            )
        ).all()
    )
    for pi in pis:
        await session.delete(pi)
    await session.flush()

    # Reorder remaining with two-phase update to satisfy UNIQUE(playlist_id, position)
    remaining = list(
        (
            await session.scalars(
                select(PlaylistItem)
                .where(PlaylistItem.playlist_id == playlist.id)
                .order_by(PlaylistItem.position.asc())
            )
        ).all()
    )
    for idx, pi in enumerate(remaining):
        pi.position = -(idx + 1)
    await session.flush()
    for pos, pi in enumerate(remaining):
        pi.position = pos

    playlist.updated_at = utcnow()
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{playlist_id}/items/reorder", response_model=PlaylistDetail)
@router.put("/{playlist_id}/items/order", response_model=PlaylistDetail)
async def reorder_playlist_items(
    playlist_id: str,
    body: ReorderPlaylistItemsRequest,
    user: RequireUser,
    session: SessionDep,
) -> PlaylistDetail:
    """Reorder items in a playlist."""
    playlist = await session.get(Playlist, playlist_id)
    if playlist is None or (user.role != "admin" and playlist.user_id != user.id):
        raise NotFoundError("Playlist not found", code="playlist_not_found")

    items = list(
        (
            await session.scalars(
                select(PlaylistItem).where(PlaylistItem.playlist_id == playlist.id)
            )
        ).all()
    )
    by_item_id = {pi.item_id: pi for pi in items}
    by_id = {pi.id: pi for pi in items}

    # Two-phase update to satisfy UNIQUE(playlist_id, position) in SQLite
    for idx, pi in enumerate(items):
        pi.position = -(idx + 1)
    await session.flush()

    for pos, target_id in enumerate(body.item_ids):
        target = by_id.get(target_id) or by_item_id.get(target_id)
        if target:
            target.position = pos

    playlist.updated_at = utcnow()
    await session.commit()
    return await _get_playlist_detail(session, user, playlist.id)


async def _get_playlist_detail(
    session: SessionDep,
    user: RequireUser,
    playlist_id: str,
) -> PlaylistDetail:
    playlist = await session.get(Playlist, playlist_id)
    if playlist is None or (user.role != "admin" and playlist.user_id != user.id):
        raise NotFoundError("Playlist not found", code="playlist_not_found")

    pis = list(
        (
            await session.scalars(
                select(PlaylistItem)
                .where(PlaylistItem.playlist_id == playlist.id)
                .order_by(PlaylistItem.position.asc())
            )
        ).all()
    )

    item_ids = [pi.item_id for pi in pis]
    if not item_ids:
        return PlaylistDetail(
            id=playlist.id,
            name=playlist.name,
            description=playlist.description,
            item_count=0,
            duration_seconds=0,
            total_duration_seconds=0.0,
            items=[],
            created_at=playlist.created_at,
            updated_at=playlist.updated_at,
        )

    media_query = select(MediaItem).where(MediaItem.id.in_(item_ids))
    if user.role != "admin":
        media_query = media_query.join(
            LibraryAccess,
            (LibraryAccess.library_id == MediaItem.library_id) & (LibraryAccess.user_id == user.id),
        )

    accessible_items = {item.id: item for item in (await session.scalars(media_query)).all()}
    states = await watch_states(session, user.id, list(accessible_items.keys()))

    details: list[PlaylistItemDetail] = []
    total_dur: int = 0

    for pi in pis:
        item = accessible_items.get(pi.item_id)
        if item is None:
            continue
        ts: TrackSummary | None = None
        if item.type == "track" and item.track is not None:
            ts = _track_summary(item, states.get(item.id))
            if ts.duration_seconds:
                total_dur += int(ts.duration_seconds)
        elif item.runtime_seconds:
            total_dur += int(item.runtime_seconds)

        summary = item_summary(item, states.get(item.id))
        details.append(
            PlaylistItemDetail(
                id=pi.id,
                position=pi.position,
                added_at=pi.added_at,
                track=ts,
                item=summary,
            )
        )

    return PlaylistDetail(
        id=playlist.id,
        name=playlist.name,
        description=playlist.description,
        item_count=len(details),
        duration_seconds=total_dur,
        total_duration_seconds=float(total_dur),
        items=details,
        created_at=playlist.created_at,
        updated_at=playlist.updated_at,
    )
