"""Music browsing, search, shelves, and playlist management endpoints."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app.api.v1.items import (
    ImageResponse,
    WatchStateResponse,
    _primary_image,
    watch_state_response,
    watch_states,
)
from app.core.deps import RequireUser, user_can_access_library
from app.core.errors import NotFoundError, ValidationError
from app.db.models import (
    Album,
    Artist,
    Genre,
    LibraryAccess,
    MediaFile,
    MediaItem,
    Track,
    User,
    UserItemState,
)
from app.db.session import SessionDep

router = APIRouter(tags=["music"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


# ------------------------------------------------------------- helper queries


def _restrict_to_accessible_libraries(query: Any, user: User) -> Any:
    """Filter MediaItem query by the libraries the user has access to."""
    if user.role == "admin":
        return query
    grant = select(LibraryAccess.library_id).where(LibraryAccess.user_id == user.id)
    return query.where(MediaItem.library_id.in_(grant))


# ------------------------------------------------------------- response models


class ArtistSummary(BaseModel):
    id: str
    name: str
    sort_name: str | None = None
    country: str | None = None
    disambiguation: str | None = None
    musicbrainz_id: str | None = None
    poster: ImageResponse | None = None
    album_count: int = 0
    track_count: int = 0


class TrackSummary(BaseModel):
    id: str
    album_id: str | None = None
    album_title: str | None = None
    artist_id: str | None = None
    artist_name: str | None = None
    title: str
    disc_number: int = 1
    track_number: int | None = None
    duration_seconds: float | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    stream_url: str
    watch_state: WatchStateResponse | None = None


class AlbumSummary(BaseModel):
    id: str
    artist_id: str | None = None
    artist_name: str | None = None
    title: str
    year: int | None = None
    release_date: str | None = None
    total_discs: int | None = 1
    total_tracks: int | None = None
    track_count: int = 0
    duration_seconds: int = 0
    genres: list[str] = []
    poster: ImageResponse | None = None
    musicbrainz_id: str | None = None
    watch_state: WatchStateResponse | None = None


class DiscTracklist(BaseModel):
    disc_number: int
    tracks: list[TrackSummary]


class AlbumDetail(BaseModel):
    id: str
    artist_id: str | None = None
    artist_name: str | None = None
    title: str
    year: int | None = None
    release_date: str | None = None
    total_discs: int | None = 1
    total_tracks: int | None = None
    duration_seconds: int = 0
    genres: list[str] = []
    poster: ImageResponse | None = None
    musicbrainz_id: str | None = None
    musicbrainz_release_group_id: str | None = None
    discs: list[DiscTracklist] = []
    watch_state: WatchStateResponse | None = None


class ArtistDetail(BaseModel):
    id: str
    name: str
    sort_name: str | None = None
    country: str | None = None
    disambiguation: str | None = None
    musicbrainz_id: str | None = None
    overview: str | None = None
    genres: list[str] = []
    poster: ImageResponse | None = None
    albums: list[AlbumSummary] = []
    top_tracks: list[TrackSummary] = []


class TrackDetail(BaseModel):
    id: str
    album_id: str | None = None
    album_title: str | None = None
    artist_id: str | None = None
    artist_name: str | None = None
    title: str
    disc_number: int = 1
    track_number: int | None = None
    duration_seconds: float | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    container: str | None = None
    replaygain_track_gain: float | None = None
    replaygain_track_peak: float | None = None
    replaygain_album_gain: float | None = None
    replaygain_album_peak: float | None = None
    musicbrainz_track_id: str | None = None
    stream_url: str
    watch_state: WatchStateResponse | None = None


class ArtistListResponse(BaseModel):
    items: list[ArtistSummary]
    total: int
    limit: int
    offset: int


class AlbumListResponse(BaseModel):
    items: list[AlbumSummary]
    total: int
    limit: int
    offset: int


class TrackListResponse(BaseModel):
    items: list[TrackSummary]
    total: int
    limit: int
    offset: int


class MusicRandomResponse(BaseModel):
    albums: list[AlbumSummary]
    tracks: list[TrackSummary]


class MusicMostPlayedResponse(BaseModel):
    albums: list[AlbumSummary]
    tracks: list[TrackSummary]


class MusicSearchResponse(BaseModel):
    artists: list[ArtistSummary]
    albums: list[AlbumSummary]
    tracks: list[TrackSummary]


# ------------------------------------------------------------- internal helpers


def _album_artist_name(album: Album | None) -> str | None:
    if album is None:
        return None
    if album.album_artist:
        return album.album_artist
    if album.artist and album.artist.item:
        return album.artist.item.title
    return None


def _track_summary(track_item: MediaItem, state: UserItemState | None) -> TrackSummary:
    tr = track_item.track
    album_id = tr.album_id if tr else None
    album_title = tr.album.item.title if tr and tr.album and tr.album.item else None
    artist_id = tr.artist_id if tr else None
    artist_name = tr.artist.item.title if tr and tr.artist and tr.artist.item else None
    return TrackSummary(
        id=track_item.id,
        album_id=album_id,
        album_title=album_title,
        artist_id=artist_id,
        artist_name=artist_name,
        title=track_item.title,
        disc_number=tr.disc_number if tr else 1,
        track_number=tr.track_number if tr else None,
        duration_seconds=tr.duration_seconds if tr else None,
        bitrate=tr.bitrate if tr else None,
        sample_rate=tr.sample_rate if tr else None,
        channels=tr.channels if tr else None,
        stream_url=f"/api/v1/stream/audio/{track_item.id}",
        watch_state=watch_state_response(state),
    )


# ------------------------------------------------------------- artist endpoints


@router.get("/artists")
async def list_artists(
    user: RequireUser,
    session: SessionDep,
    query: Annotated[str | None, Query(max_length=255)] = None,
    sort: Annotated[Literal["name", "added"], Query()] = "name",
    order: Annotated[Literal["asc", "desc"], Query()] = "asc",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ArtistListResponse:
    """List artists with pagination and counts."""
    base = select(MediaItem).where(MediaItem.type == "artist")
    base = _restrict_to_accessible_libraries(base, user)

    if query:
        clean = query.strip()
        if clean:
            base = base.where(MediaItem.title.ilike(f"%{clean}%"))

    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0

    col: Any
    if sort == "name":
        col = MediaItem.sort_title.asc() if order == "asc" else MediaItem.sort_title.desc()
    else:
        col = MediaItem.added_at.asc() if order == "asc" else MediaItem.added_at.desc()

    base = base.options(
        joinedload(MediaItem.artist).lazyload(Artist.albums),
        joinedload(MediaItem.artist).lazyload(Artist.tracks),
        selectinload(MediaItem.images),
    )
    artist_items = list(
        await session.scalars(base.order_by(col, MediaItem.id).offset(offset).limit(limit))
    )
    artist_ids = [a.id for a in artist_items]

    album_counts: dict[str, int] = {}
    track_counts: dict[str, int] = {}
    if artist_ids:
        # Alb counts
        alb_q = (
            select(Album.artist_id, func.count(Album.item_id))
            .where(Album.artist_id.in_(artist_ids))
            .group_by(Album.artist_id)
        )
        album_counts = {
            row[0]: int(row[1])
            for row in (await session.execute(alb_q)).all()
            if row[0] is not None
        }

        # Track counts
        tr_q = (
            select(Track.artist_id, func.count(Track.item_id))
            .where(Track.artist_id.in_(artist_ids))
            .group_by(Track.artist_id)
        )
        track_counts = {
            row[0]: int(row[1]) for row in (await session.execute(tr_q)).all() if row[0] is not None
        }

    items = [
        ArtistSummary(
            id=a.id,
            name=a.title,
            sort_name=a.artist.sort_name if a.artist else a.sort_title,
            country=a.artist.country if a.artist else None,
            disambiguation=a.artist.disambiguation if a.artist else None,
            musicbrainz_id=a.artist.musicbrainz_id if a.artist else None,
            poster=_primary_image(a, "poster"),
            album_count=album_counts.get(a.id, 0),
            track_count=track_counts.get(a.id, 0),
        )
        for a in artist_items
    ]
    return ArtistListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/artists/{artist_id}")
async def get_artist_detail(
    artist_id: str,
    user: RequireUser,
    session: SessionDep,
) -> ArtistDetail:
    """Return artist details with albums and top tracks."""
    item = await session.get(MediaItem, artist_id)
    if (
        item is None
        or item.type != "artist"
        or not await user_can_access_library(session, user, item.library_id)
    ):
        raise NotFoundError("Artist not found", code="artist_not_found")

    # Artist albums
    alb_q = (
        select(MediaItem)
        .join(Album, Album.item_id == MediaItem.id)
        .where(Album.artist_id == artist_id)
        .order_by(Album.release_date.desc(), MediaItem.year.desc(), MediaItem.title.asc())
    )
    alb_items = list(await session.scalars(alb_q))
    album_ids = [a.id for a in alb_items]

    track_counts: dict[str, int] = {}
    duration_sums: dict[str, int] = {}
    if album_ids:
        tr_stats = (
            select(
                Track.album_id,
                func.count(Track.item_id),
                func.coalesce(func.sum(Track.duration_seconds), 0),
            )
            .where(Track.album_id.in_(album_ids))
            .group_by(Track.album_id)
        )
        for alb_id, count, dur in (await session.execute(tr_stats)).all():
            if alb_id is not None:
                track_counts[alb_id] = int(count)
                duration_sums[alb_id] = int(dur or 0)

    alb_states = await watch_states(session, user.id, album_ids)
    albums = [
        AlbumSummary(
            id=a.id,
            artist_id=artist_id,
            artist_name=item.title,
            title=a.title,
            year=a.year,
            release_date=a.album.release_date if a.album else None,
            total_discs=a.album.total_discs if a.album else 1,
            total_tracks=a.album.total_tracks if a.album else None,
            track_count=track_counts.get(a.id, 0),
            duration_seconds=duration_sums.get(a.id, 0),
            genres=sorted(g.name for g in a.genres),
            poster=_primary_image(a, "poster"),
            musicbrainz_id=a.album.musicbrainz_id if a.album else None,
            watch_state=watch_state_response(alb_states.get(a.id)),
        )
        for a in alb_items
    ]

    # Top tracks (up to 10 by play count or track position)
    top_tr_q = (
        select(MediaItem)
        .join(Track, Track.item_id == MediaItem.id)
        .outerjoin(
            UserItemState,
            (UserItemState.item_id == MediaItem.id) & (UserItemState.user_id == user.id),
        )
        .where(Track.artist_id == artist_id)
        .order_by(
            func.coalesce(UserItemState.play_count, 0).desc(),
            Track.disc_number.asc(),
            Track.track_number.asc(),
        )
        .limit(10)
    )
    top_items = list(await session.scalars(top_tr_q))
    top_states = await watch_states(session, user.id, [t.id for t in top_items])
    top_tracks = [_track_summary(t, top_states.get(t.id)) for t in top_items]

    return ArtistDetail(
        id=item.id,
        name=item.title,
        sort_name=item.artist.sort_name if item.artist else item.sort_title,
        country=item.artist.country if item.artist else None,
        disambiguation=item.artist.disambiguation if item.artist else None,
        musicbrainz_id=item.artist.musicbrainz_id if item.artist else None,
        overview=item.overview,
        genres=sorted(g.name for g in item.genres),
        poster=_primary_image(item, "poster"),
        albums=albums,
        top_tracks=top_tracks,
    )


# -------------------------------------------------------------- album endpoints


@router.get("/albums")
async def list_albums(
    user: RequireUser,
    session: SessionDep,
    artist_id: Annotated[str | None, Query(max_length=36)] = None,
    genre: Annotated[str | None, Query(max_length=64)] = None,
    query: Annotated[str | None, Query(max_length=255)] = None,
    sort: Annotated[Literal["title", "release_date", "added"], Query()] = "title",
    order: Annotated[Literal["asc", "desc"], Query()] = "asc",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AlbumListResponse:
    """List albums with filtering and pagination."""
    base = (
        select(MediaItem)
        .join(Album, Album.item_id == MediaItem.id)
        .where(MediaItem.type == "album")
    )
    base = _restrict_to_accessible_libraries(base, user)

    if artist_id:
        base = base.where(Album.artist_id == artist_id)
    if genre:
        clean_g = genre.strip().casefold()
        if clean_g:
            base = base.where(MediaItem.genres.any(func.lower(Genre.name) == clean_g))
    if query:
        clean_q = query.strip()
        if clean_q:
            base = base.where(MediaItem.title.ilike(f"%{clean_q}%"))

    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0

    col: Any
    if sort == "title":
        col = MediaItem.sort_title.asc() if order == "asc" else MediaItem.sort_title.desc()
    elif sort == "release_date":
        col = Album.release_date.asc() if order == "asc" else Album.release_date.desc()
    else:
        col = MediaItem.added_at.asc() if order == "asc" else MediaItem.added_at.desc()

    base = base.options(
        joinedload(MediaItem.album).joinedload(Album.artist).lazyload(Artist.albums),
        joinedload(MediaItem.album).joinedload(Album.artist).lazyload(Artist.tracks),
        joinedload(MediaItem.album).lazyload(Album.tracks),
        selectinload(MediaItem.genres),
        selectinload(MediaItem.images),
    )
    alb_items = list(
        await session.scalars(base.order_by(col, MediaItem.id).offset(offset).limit(limit))
    )
    album_ids = [a.id for a in alb_items]

    track_counts: dict[str, int] = {}
    duration_sums: dict[str, int] = {}
    if album_ids:
        tr_stats = (
            select(
                Track.album_id,
                func.count(Track.item_id),
                func.coalesce(func.sum(Track.duration_seconds), 0),
            )
            .where(Track.album_id.in_(album_ids))
            .group_by(Track.album_id)
        )
        for a_id, count, dur in (await session.execute(tr_stats)).all():
            if a_id is not None:
                track_counts[a_id] = int(count)
                duration_sums[a_id] = int(dur or 0)

    alb_states = await watch_states(session, user.id, album_ids)
    items = [
        AlbumSummary(
            id=a.id,
            artist_id=a.album.artist_id if a.album else None,
            artist_name=_album_artist_name(a.album),
            title=a.title,
            year=a.year,
            release_date=a.album.release_date if a.album else None,
            total_discs=a.album.total_discs if a.album else 1,
            total_tracks=a.album.total_tracks if a.album else None,
            track_count=track_counts.get(a.id, 0),
            duration_seconds=duration_sums.get(a.id, 0),
            genres=sorted(g.name for g in a.genres),
            poster=_primary_image(a, "poster"),
            musicbrainz_id=a.album.musicbrainz_id if a.album else None,
            watch_state=watch_state_response(alb_states.get(a.id)),
        )
        for a in alb_items
    ]
    return AlbumListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/albums/{album_id}")
async def get_album_detail(
    album_id: str,
    user: RequireUser,
    session: SessionDep,
) -> AlbumDetail:
    """Return album details with tracklist grouped by disc."""
    item = await session.get(MediaItem, album_id)
    if (
        item is None
        or item.type != "album"
        or not await user_can_access_library(session, user, item.library_id)
    ):
        raise NotFoundError("Album not found", code="album_not_found")

    album = item.album
    if album is None:
        raise NotFoundError("Album not found", code="album_not_found")

    # Fetch tracks
    tr_q = (
        select(MediaItem)
        .join(Track, Track.item_id == MediaItem.id)
        .where(Track.album_id == album_id)
        .order_by(Track.disc_number.asc(), Track.track_number.asc(), MediaItem.title.asc())
    )
    track_items = list(await session.scalars(tr_q))
    track_states = await watch_states(session, user.id, [t.id for t in track_items])

    total_duration = 0
    grouped_tracks: dict[int, list[TrackSummary]] = {}
    for t in track_items:
        summary = _track_summary(t, track_states.get(t.id))
        if summary.duration_seconds:
            total_duration += int(summary.duration_seconds)
        disc = summary.disc_number or 1
        if disc not in grouped_tracks:
            grouped_tracks[disc] = []
        grouped_tracks[disc].append(summary)

    discs = [
        DiscTracklist(disc_number=disc, tracks=tracks)
        for disc, tracks in sorted(grouped_tracks.items())
    ]

    alb_state = await watch_states(session, user.id, [album_id])
    artist_name = album.album_artist or (
        album.artist.item.title if album.artist and album.artist.item else None
    )

    return AlbumDetail(
        id=item.id,
        artist_id=album.artist_id,
        artist_name=artist_name,
        title=item.title,
        year=item.year,
        release_date=album.release_date,
        total_discs=album.total_discs or (len(discs) if discs else 1),
        total_tracks=album.total_tracks or len(track_items),
        duration_seconds=total_duration,
        genres=sorted(g.name for g in item.genres),
        poster=_primary_image(item, "poster"),
        musicbrainz_id=album.musicbrainz_id,
        musicbrainz_release_group_id=album.musicbrainz_release_group_id,
        discs=discs,
        watch_state=watch_state_response(alb_state.get(album_id)),
    )


# -------------------------------------------------------------- track endpoints


@router.get("/tracks")
async def list_tracks(
    user: RequireUser,
    session: SessionDep,
    album_id: Annotated[str | None, Query(max_length=36)] = None,
    artist_id: Annotated[str | None, Query(max_length=36)] = None,
    query: Annotated[str | None, Query(max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TrackListResponse:
    """List tracks with optional album or artist filtering."""
    base = (
        select(MediaItem)
        .join(Track, Track.item_id == MediaItem.id)
        .where(MediaItem.type == "track")
    )
    base = _restrict_to_accessible_libraries(base, user)

    if album_id:
        base = base.where(Track.album_id == album_id)
    if artist_id:
        base = base.where(Track.artist_id == artist_id)
    if query:
        clean_q = query.strip()
        if clean_q:
            base = base.where(MediaItem.title.ilike(f"%{clean_q}%"))

    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    base = base.options(
        joinedload(MediaItem.track).joinedload(Track.album).joinedload(Album.item),
        joinedload(MediaItem.track).joinedload(Track.artist).joinedload(Artist.item),
    )
    track_items = list(
        await session.scalars(
            base.order_by(Track.album_id, Track.disc_number, Track.track_number, MediaItem.id)
            .offset(offset)
            .limit(limit)
        )
    )

    states = await watch_states(session, user.id, [t.id for t in track_items])
    items = [_track_summary(t, states.get(t.id)) for t in track_items]
    return TrackListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/tracks/{track_id}")
async def get_track_detail(
    track_id: str,
    user: RequireUser,
    session: SessionDep,
) -> TrackDetail:
    """Return track details including audio technical properties and ReplayGain."""
    item = await session.get(MediaItem, track_id)
    if (
        item is None
        or item.type != "track"
        or not await user_can_access_library(session, user, item.library_id)
    ):
        raise NotFoundError("Track not found", code="track_not_found")

    tr = item.track
    if tr is None:
        raise NotFoundError("Track not found", code="track_not_found")

    album_title = tr.album.item.title if tr.album and tr.album.item else None
    artist_name = tr.artist.item.title if tr.artist and tr.artist.item else None

    # Fetch file container
    container: str | None = None
    if tr.file_id:
        f = await session.get(MediaFile, tr.file_id)
        if f:
            container = f.container

    states = await watch_states(session, user.id, [track_id])

    return TrackDetail(
        id=item.id,
        album_id=tr.album_id,
        album_title=album_title,
        artist_id=tr.artist_id,
        artist_name=artist_name,
        title=item.title,
        disc_number=tr.disc_number,
        track_number=tr.track_number,
        duration_seconds=tr.duration_seconds,
        bitrate=tr.bitrate,
        sample_rate=tr.sample_rate,
        channels=tr.channels,
        container=container,
        replaygain_track_gain=tr.replaygain_track_gain,
        replaygain_track_peak=tr.replaygain_track_peak,
        replaygain_album_gain=tr.replaygain_album_gain,
        replaygain_album_peak=tr.replaygain_album_peak,
        musicbrainz_track_id=tr.musicbrainz_track_id,
        stream_url=f"/api/v1/stream/audio/{item.id}",
        watch_state=watch_state_response(states.get(track_id)),
    )


# ------------------------------------------------------------- shelves / search


@router.get("/music/random")
async def random_music(
    user: RequireUser,
    session: SessionDep,
    albums_limit: Annotated[int, Query(ge=1, le=50)] = 10,
    tracks_limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> MusicRandomResponse:
    """Return random albums and tracks."""
    alb_q = (
        select(MediaItem)
        .join(Album, Album.item_id == MediaItem.id)
        .where(MediaItem.type == "album")
    )
    alb_q = (
        _restrict_to_accessible_libraries(alb_q, user).order_by(func.random()).limit(albums_limit)
    )
    alb_items = list(await session.scalars(alb_q))
    alb_states = await watch_states(session, user.id, [a.id for a in alb_items])

    albums = [
        AlbumSummary(
            id=a.id,
            artist_id=a.album.artist_id if a.album else None,
            artist_name=_album_artist_name(a.album),
            title=a.title,
            year=a.year,
            release_date=a.album.release_date if a.album else None,
            total_discs=a.album.total_discs if a.album else 1,
            total_tracks=a.album.total_tracks if a.album else None,
            poster=_primary_image(a, "poster"),
            musicbrainz_id=a.album.musicbrainz_id if a.album else None,
            watch_state=watch_state_response(alb_states.get(a.id)),
        )
        for a in alb_items
    ]

    tr_q = (
        select(MediaItem)
        .join(Track, Track.item_id == MediaItem.id)
        .where(MediaItem.type == "track")
    )
    tr_q = _restrict_to_accessible_libraries(tr_q, user).order_by(func.random()).limit(tracks_limit)
    tr_items = list(await session.scalars(tr_q))
    tr_states = await watch_states(session, user.id, [t.id for t in tr_items])
    tracks = [_track_summary(t, tr_states.get(t.id)) for t in tr_items]

    return MusicRandomResponse(albums=albums, tracks=tracks)


@router.get("/music/recently-added")
async def recently_added_albums(
    user: RequireUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[AlbumSummary]:
    """Return recently added albums."""
    alb_q = (
        select(MediaItem)
        .join(Album, Album.item_id == MediaItem.id)
        .where(MediaItem.type == "album")
    )
    alb_q = (
        _restrict_to_accessible_libraries(alb_q, user)
        .order_by(MediaItem.added_at.desc())
        .limit(limit)
    )
    alb_items = list(await session.scalars(alb_q))
    alb_states = await watch_states(session, user.id, [a.id for a in alb_items])

    return [
        AlbumSummary(
            id=a.id,
            artist_id=a.album.artist_id if a.album else None,
            artist_name=_album_artist_name(a.album),
            title=a.title,
            year=a.year,
            release_date=a.album.release_date if a.album else None,
            total_discs=a.album.total_discs if a.album else 1,
            total_tracks=a.album.total_tracks if a.album else None,
            poster=_primary_image(a, "poster"),
            musicbrainz_id=a.album.musicbrainz_id if a.album else None,
            watch_state=watch_state_response(alb_states.get(a.id)),
        )
        for a in alb_items
    ]


@router.get("/music/most-played")
async def most_played_music(
    user: RequireUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> MusicMostPlayedResponse:
    """Return user's most played tracks and albums."""
    # Most played tracks
    tr_q = (
        select(MediaItem)
        .join(Track, Track.item_id == MediaItem.id)
        .join(
            UserItemState,
            (UserItemState.item_id == MediaItem.id) & (UserItemState.user_id == user.id),
        )
        .where(MediaItem.type == "track", UserItemState.play_count > 0)
        .order_by(UserItemState.play_count.desc(), UserItemState.last_played_at.desc())
        .limit(limit)
    )
    tr_q = _restrict_to_accessible_libraries(tr_q, user)
    tr_items = list(await session.scalars(tr_q))
    tr_states = await watch_states(session, user.id, [t.id for t in tr_items])
    tracks = [_track_summary(t, tr_states.get(t.id)) for t in tr_items]

    # Most played albums (sum of track play counts or album user item state)
    alb_q = (
        select(MediaItem)
        .join(Album, Album.item_id == MediaItem.id)
        .outerjoin(
            UserItemState,
            (UserItemState.item_id == MediaItem.id) & (UserItemState.user_id == user.id),
        )
        .where(MediaItem.type == "album")
        .order_by(func.coalesce(UserItemState.play_count, 0).desc(), MediaItem.added_at.desc())
        .limit(limit)
    )
    alb_q = _restrict_to_accessible_libraries(alb_q, user)
    alb_items = list(await session.scalars(alb_q))
    alb_states = await watch_states(session, user.id, [a.id for a in alb_items])
    albums = [
        AlbumSummary(
            id=a.id,
            artist_id=a.album.artist_id if a.album else None,
            artist_name=_album_artist_name(a.album),
            title=a.title,
            year=a.year,
            release_date=a.album.release_date if a.album else None,
            total_discs=a.album.total_discs if a.album else 1,
            total_tracks=a.album.total_tracks if a.album else None,
            poster=_primary_image(a, "poster"),
            musicbrainz_id=a.album.musicbrainz_id if a.album else None,
            watch_state=watch_state_response(alb_states.get(a.id)),
        )
        for a in alb_items
    ]

    return MusicMostPlayedResponse(albums=albums, tracks=tracks)


@router.get("/music/search")
async def search_music(
    user: RequireUser,
    session: SessionDep,
    query: Annotated[str | None, Query(max_length=255)] = None,
    q: Annotated[str | None, Query(max_length=255)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> MusicSearchResponse:
    """Search across artist names, album titles, and track titles."""
    term = (query or q or "").strip()
    if not term:
        raise ValidationError("Search query cannot be empty", code="query_required")
    pattern = f"%{term}%"

    # Artists
    art_q = select(MediaItem).where(MediaItem.type == "artist", MediaItem.title.ilike(pattern))
    art_q = _restrict_to_accessible_libraries(art_q, user).limit(limit)
    art_items = list(await session.scalars(art_q))
    artists = [
        ArtistSummary(
            id=a.id,
            name=a.title,
            sort_name=a.artist.sort_name if a.artist else a.sort_title,
            country=a.artist.country if a.artist else None,
            disambiguation=a.artist.disambiguation if a.artist else None,
            musicbrainz_id=a.artist.musicbrainz_id if a.artist else None,
            poster=_primary_image(a, "poster"),
        )
        for a in art_items
    ]

    # Albums
    alb_q = (
        select(MediaItem)
        .join(Album, Album.item_id == MediaItem.id)
        .where(
            MediaItem.type == "album",
            or_(MediaItem.title.ilike(pattern), Album.album_artist.ilike(pattern)),
        )
    )
    alb_q = _restrict_to_accessible_libraries(alb_q, user).limit(limit)
    alb_items = list(await session.scalars(alb_q))
    alb_states = await watch_states(session, user.id, [a.id for a in alb_items])
    albums = [
        AlbumSummary(
            id=a.id,
            artist_id=a.album.artist_id if a.album else None,
            artist_name=_album_artist_name(a.album),
            title=a.title,
            year=a.year,
            release_date=a.album.release_date if a.album else None,
            total_discs=a.album.total_discs if a.album else 1,
            total_tracks=a.album.total_tracks if a.album else None,
            poster=_primary_image(a, "poster"),
            musicbrainz_id=a.album.musicbrainz_id if a.album else None,
            watch_state=watch_state_response(alb_states.get(a.id)),
        )
        for a in alb_items
    ]

    # Tracks
    tr_q = (
        select(MediaItem)
        .join(Track, Track.item_id == MediaItem.id)
        .where(MediaItem.type == "track", MediaItem.title.ilike(pattern))
    )
    tr_q = _restrict_to_accessible_libraries(tr_q, user).limit(limit)
    tr_items = list(await session.scalars(tr_q))
    tr_states = await watch_states(session, user.id, [t.id for t in tr_items])
    tracks = [_track_summary(t, tr_states.get(t.id)) for t in tr_items]

    return MusicSearchResponse(artists=artists, albums=albums, tracks=tracks)
