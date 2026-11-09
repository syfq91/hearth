"""Movie browse and item-detail endpoints with cursor pagination."""

import base64
import binascii
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.core.deps import (
    LibraryAccessDep,
    RequireUser,
    user_can_access_library,
)
from app.core.errors import NotFoundError, ValidationError
from app.db.models import (
    Episode,
    Genre,
    ItemPerson,
    Library,
    MediaFile,
    MediaItem,
    Person,
    UserItemState,
)
from app.db.models import (
    Image as ImageModel,
)
from app.db.session import SessionDep
from app.services.ratings import apply_rating_filter, is_rating_permitted
from app.services.subtitles import (
    discover_subtitles,
    encode_subtitle_id,
    subtitle_language,
)

router = APIRouter(tags=["items"])

SortKey = Literal["title", "added", "year", "rating"]
SortOrder = Literal["asc", "desc"]

_DEFAULT_ORDER: dict[str, SortOrder] = {
    "title": "asc",
    "added": "desc",
    "year": "desc",
    "rating": "desc",
}
_NULL_YEAR = 9999
_NULL_RATING = 11.0


class ImageResponse(BaseModel):
    """A cached artwork original and the URL that serves variants of it."""

    id: str
    kind: str
    width: int
    height: int
    blurhash: str | None = None
    is_primary: bool = False
    url: str


class WatchStateResponse(BaseModel):
    """The current user's progress and flags for an item."""

    watched: bool = False
    position_ms: int = 0
    favorite: bool = False
    rating: int | None = None
    play_count: int = 0
    last_played_at: datetime | None = None


class PersonCreditResponse(BaseModel):
    """One cast or crew credit."""

    name: str
    role: str
    character: str | None = None
    ordering: int = 0


class SubtitleResponse(BaseModel):
    """A playable external subtitle track."""

    id: str
    name: str
    format: str
    language: str | None = None
    url: str


class MediaFileResponse(BaseModel):
    """A file version without exposing its host path."""

    id: str
    container: str | None = None
    duration_seconds: float | None = None
    bit_rate: int | None = None
    size_bytes: int
    version_label: str | None = None
    probe_error: str | None = None
    missing: bool = False
    subtitles: list[SubtitleResponse] = []


class ItemSummary(BaseModel):
    """A catalog card."""

    id: str
    type: str = "movie"
    title: str
    sort_title: str
    year: int | None = None
    overview: str | None = None
    rating: float | None = None
    content_rating: str | None = None
    runtime_seconds: int | None = None
    added_at: datetime
    match_state: str
    genres: list[str] = []
    poster: ImageResponse | None = None
    backdrop: ImageResponse | None = None
    watch_state: WatchStateResponse
    series_id: str | None = None
    series_title: str | None = None
    series_poster: ImageResponse | None = None
    season_number: int | None = None
    episode_number: int | None = None
    episode_count: int | None = None
    volume: str | None = None


class ItemDetail(ItemSummary):
    """Everything the detail page needs for one movie."""

    original_title: str | None = None
    tagline: str | None = None
    release_date: str | None = None
    edition: str | None = None
    part: int | None = None
    provider_ids: dict[str, str] = {}
    files: list[MediaFileResponse] = []
    people: list[PersonCreditResponse] = []
    images: list[ImageResponse] = []


class ItemPage(BaseModel):
    """One page of items plus an opaque cursor for the next page."""

    items: list[ItemSummary]
    next_cursor: str | None = None
    has_more: bool = False


class LibraryFolder(BaseModel):
    """A directory inside a book library and how many books it holds."""

    name: str
    path: str
    item_count: int
    poster: ImageResponse | None = None


class LibraryFolderPage(BaseModel):
    """The subfolders of one directory in a book library."""

    path: str
    parent: str | None = None
    folders: list[LibraryFolder] = []


@router.get("/libraries/{library_id}/items")
async def list_library_items(
    library: LibraryAccessDep,
    user: RequireUser,
    session: SessionDep,
    sort: Annotated[SortKey, Query()] = "title",
    order: Annotated[SortOrder | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    genre: Annotated[str | None, Query(max_length=64)] = None,
    year: Annotated[int | None, Query(ge=1800, le=2100)] = None,
    watched: Annotated[bool | None, Query()] = None,
    folder: Annotated[
        str | None,
        Query(
            max_length=1024,
            description="Book libraries only: a folder path from /folders (default: library root)",
        ),
    ] = None,
) -> ItemPage:
    """List a library's movies, sorted and filtered with keyset pagination.

    Book libraries keep their directory structure: the list covers one folder
    at a time, defaulting to the library root. ``folder`` is ignored for other
    library types.
    """
    direction: SortOrder = order or _DEFAULT_ORDER[sort]
    key_expression = _sort_expression(sort, direction)

    if library.type == "tv":
        item_type = "series"
    elif library.type == "book":
        item_type = "book"
    elif library.type == "music":
        item_type = "album"
    else:
        item_type = "movie"
    query = select(MediaItem).where(
        MediaItem.library_id == library.id,
        MediaItem.type == item_type,
    )
    query = apply_rating_filter(query, user)
    if genre is not None:
        wanted = genre.strip().casefold()
        if wanted:
            query = query.where(MediaItem.genres.any(func.lower(Genre.name) == wanted))
    if year is not None:
        query = query.where(MediaItem.year == year)
    if watched is not None:
        played = MediaItem.user_states.any(
            and_(
                UserItemState.user_id == user.id,
                UserItemState.played.is_(True),
            )
        )
        query = query.where(played if watched else ~played)
    if library.type == "book":
        query = query.where(_in_book_folder(library, _clean_folder(folder)))

    if cursor is not None:
        cursor_key, cursor_id = _decode_cursor(cursor, sort=sort, direction=direction)
        if direction == "asc":
            query = query.where(
                or_(
                    key_expression > cursor_key,
                    and_(key_expression == cursor_key, MediaItem.id > cursor_id),
                )
            )
        else:
            query = query.where(
                or_(
                    key_expression < cursor_key,
                    and_(key_expression == cursor_key, MediaItem.id < cursor_id),
                )
            )

    if direction == "asc":
        query = query.order_by(key_expression.asc(), MediaItem.id.asc())
    else:
        query = query.order_by(key_expression.desc(), MediaItem.id.desc())
    query = query.options(selectinload(MediaItem.images), selectinload(MediaItem.genres))
    rows = list(await session.scalars(query.limit(limit + 1)))

    has_more = len(rows) > limit
    page_items = rows[:limit]
    states = await watch_states(session, user.id, [item.id for item in page_items])

    next_cursor = None
    if has_more and page_items:
        last = page_items[-1]
        next_cursor = _encode_cursor(
            sort=sort,
            direction=direction,
            key=_item_key(last, sort, direction),
            item_id=last.id,
        )

    return ItemPage(
        items=[item_summary(item, states.get(item.id)) for item in page_items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/libraries/{library_id}/folders")
async def list_library_folders(
    library: LibraryAccessDep,
    user: RequireUser,
    session: SessionDep,
    path: Annotated[str | None, Query(max_length=1024)] = None,
) -> LibraryFolderPage:
    """List the immediate subfolders of one directory in a book library.

    Folder paths are relative to the library root and are derived from the
    files on disk, so they mirror the directory tree the books came from.
    """
    if library.type != "book":
        raise ValidationError(
            "Folders are only available for book libraries", code="not_a_book_library"
        )

    current = _clean_folder(path)
    prefix = f"{current}/" if current else ""
    counts: dict[str, int] = {}
    candidate_items: dict[str, list[str]] = {}

    for item_id, item_folders in (await _book_folders(session, library)).items():
        children: set[str] = set()
        for item_folder in item_folders:
            remainder = item_folder[len(prefix) :] if item_folder.startswith(prefix) else ""
            if remainder:
                children.add(remainder.split("/", 1)[0])
        for child in children:
            counts[child] = counts.get(child, 0) + 1
            candidates = candidate_items.setdefault(child, [])
            if len(candidates) < 10:
                candidates.append(item_id)

    all_candidate_ids = {item_id for ids in candidate_items.values() for item_id in ids}
    posters_by_item: dict[str, ImageResponse] = {}
    if all_candidate_ids:
        image_rows = await session.execute(
            select(ImageModel).where(
                ImageModel.item_id.in_(all_candidate_ids),
                ImageModel.kind == "poster",
            )
        )
        for img in image_rows.scalars().all():
            if img.item_id not in posters_by_item or img.is_primary:
                posters_by_item[img.item_id] = _image_response(img)

    child_posters: dict[str, ImageResponse | None] = {}
    for child, item_ids in candidate_items.items():
        child_posters[child] = next(
            (posters_by_item[item_id] for item_id in item_ids if item_id in posters_by_item),
            None,
        )

    folders = [
        LibraryFolder(
            name=name,
            path=f"{prefix}{name}",
            item_count=count,
            poster=child_posters.get(name),
        )
        for name, count in sorted(counts.items(), key=lambda entry: entry[0].casefold())
    ]
    parent = current.rpartition("/")[0] if current else None
    return LibraryFolderPage(path=current, parent=parent, folders=folders)


@router.get(
    "/items/{item_id}",
    response_model=ItemDetail,
    responses={
        200: {"model": ItemDetail, "description": "Item detail"},
        304: {"description": "Item unchanged (ETag matched)"},
    },
)
async def get_item(
    item_id: str,
    user: RequireUser,
    session: SessionDep,
    request: Request,
    fields: Annotated[
        str | None,
        Query(description="Comma-separated list of field names to include", max_length=500),
    ] = None,
) -> Response:
    """Return one item if the current user can access its library."""
    res = await session.execute(
        select(MediaItem)
        .options(
            selectinload(MediaItem.movie),
            selectinload(MediaItem.series),
            selectinload(MediaItem.episode).selectinload(Episode.file),
            selectinload(MediaItem.files),
            selectinload(MediaItem.images),
        )
        .where(MediaItem.id == item_id)
    )
    item = res.scalar_one_or_none()
    if (
        item is None
        or not await user_can_access_library(session, user, item.library_id)
        or not is_rating_permitted(item.content_rating, user.max_rating)
    ):
        raise NotFoundError("Item not found", code="item_not_found")

    state = await session.scalar(
        select(UserItemState).where(
            UserItemState.user_id == user.id,
            UserItemState.item_id == item.id,
        )
    )
    people = await _people(session, item.id)
    summary = item_summary(item, state)
    movie = item.movie
    series = item.series
    episode = item.episode

    original_title = movie.original_title if movie else (series.original_title if series else None)
    release_date = movie.release_date if movie else (episode.air_date if episode else None)

    files_list = (
        sorted(item.files, key=lambda file: file.path)
        if item.files
        else ([episode.file] if episode and episode.file else [])
    )

    detail = ItemDetail(
        **summary.model_dump(),
        original_title=original_title,
        tagline=movie.tagline if movie else None,
        release_date=release_date,
        edition=movie.edition if movie else None,
        part=movie.part if movie else None,
        provider_ids={str(key): str(value) for key, value in (item.provider_ids or {}).items()},
        files=[
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
            for media_file in files_list
        ],
        people=people,
        images=[_image_response(image) for image in _ordered_images(item)],
    )

    data = detail.model_dump(mode="json")
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    etag = f'"{hashlib.sha256(raw).hexdigest()[:16]}"'

    if_none_match = request.headers.get("if-none-match")
    if if_none_match and (if_none_match == "*" or if_none_match.strip('"') == etag.strip('"')):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})

    if fields is not None:
        wanted = {f.strip() for f in fields.split(",") if f.strip()}
        if wanted:
            wanted.add("id")
            wanted.add("type")
            filtered = {k: v for k, v in data.items() if k in wanted}
            return JSONResponse(content=filtered, headers={"ETag": etag})

    return JSONResponse(content=data, headers={"ETag": etag})


# ------------------------------------------------------------------- helpers


def _clean_folder(folder: str | None) -> str:
    """Normalize a library-relative folder path, rejecting escapes."""
    if not folder:
        return ""
    parts = [part for part in folder.replace("\\", "/").split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValidationError("Folder path cannot leave the library root", code="invalid_folder")
    return "/".join(parts)


def _relative_folder(path: Path, roots: list[Path]) -> str | None:
    """Return the folder holding ``path`` relative to its library root."""
    for root in sorted(roots, key=lambda item: len(item.parts), reverse=True):
        try:
            relative = path.parent.relative_to(root).as_posix()
        except ValueError:
            continue
        return "" if relative == "." else relative
    return None


async def _book_folders(session: AsyncSession, library: Library) -> dict[str, set[str]]:
    """Map every book in the library to the folders that hold its files.

    Keys are ordered by MediaItem.sort_title.asc(), MediaItem.id.asc().
    """
    rows = await session.execute(
        select(MediaItem.id, MediaFile.path)
        .join(MediaFile, MediaFile.item_id == MediaItem.id)
        .where(
            MediaItem.library_id == library.id,
            MediaItem.type == "book",
            MediaFile.missing_since.is_(None),
        )
        .order_by(MediaItem.sort_title.asc(), MediaItem.id.asc())
    )
    roots = [Path(root.path) for root in library.paths]
    folders: dict[str, set[str]] = {}
    for item_id, file_path in rows.all():
        folder = _relative_folder(Path(file_path), roots)
        if folder is not None:
            folders.setdefault(item_id, set()).add(folder)
    return folders


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards in a literal path prefix."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _in_book_folder(library: Library, folder: str) -> ColumnElement[bool]:
    """Match books with a file directly inside ``folder``."""
    conditions = []
    for root in library.paths:
        base = _escape_like(str(Path(root.path) / folder) if folder else str(Path(root.path)))
        prefix = f"{base}/%"
        conditions.append(
            and_(
                MediaFile.path.like(prefix, escape="\\"),
                MediaFile.path.not_like(f"{prefix}/%", escape="\\"),
            )
        )
    return MediaItem.files.any(and_(MediaFile.missing_since.is_(None), or_(*conditions)))


def _sort_expression(sort: str, direction: SortOrder) -> Any:
    if sort == "title":
        return cast(ColumnElement[Any], MediaItem.sort_title)
    if sort == "added":
        return cast(ColumnElement[Any], MediaItem.added_at)
    if sort == "year":
        year_sentinel = -_NULL_YEAR if direction == "desc" else _NULL_YEAR
        return func.coalesce(MediaItem.year, year_sentinel)
    rating_sentinel = -_NULL_RATING if direction == "desc" else _NULL_RATING
    return func.coalesce(MediaItem.rating, rating_sentinel)


def _item_key(item: MediaItem, sort: str, direction: SortOrder) -> Any:
    if sort == "title":
        return item.sort_title
    if sort == "added":
        return item.added_at
    if sort == "year":
        return (
            item.year
            if item.year is not None
            else (-_NULL_YEAR if direction == "desc" else _NULL_YEAR)
        )
    return (
        item.rating
        if item.rating is not None
        else (-_NULL_RATING if direction == "desc" else _NULL_RATING)
    )


def _encode_cursor(*, sort: str, direction: SortOrder, key: Any, item_id: str) -> str:
    value = key.isoformat() if isinstance(key, datetime) else key
    payload = {"s": sort, "d": direction, "k": value, "id": item_id}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str, *, sort: str, direction: SortOrder) -> tuple[Any, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise ValidationError("Invalid cursor", code="invalid_cursor") from None
    if not isinstance(payload, dict) or payload.get("s") != sort or payload.get("d") != direction:
        raise ValidationError("Cursor does not match the requested sort", code="invalid_cursor")
    item_id = payload.get("id")
    value = payload.get("k")
    if not isinstance(item_id, str) or not item_id:
        raise ValidationError("Invalid cursor", code="invalid_cursor")
    if sort == "title":
        if not isinstance(value, str):
            raise ValidationError("Invalid cursor", code="invalid_cursor")
    elif sort == "added":
        try:
            value = datetime.fromisoformat(str(value))
        except ValueError:
            raise ValidationError("Invalid cursor", code="invalid_cursor") from None
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("Invalid cursor", code="invalid_cursor")
    return value, item_id


async def watch_states(
    session: AsyncSession, user_id: str, item_ids: list[str]
) -> dict[str, UserItemState]:
    if not item_ids:
        return {}
    rows = await session.scalars(
        select(UserItemState).where(
            UserItemState.user_id == user_id,
            UserItemState.item_id.in_(item_ids),
        )
    )
    return {state.item_id: state for state in rows}


def watch_state_response(state: UserItemState | None) -> WatchStateResponse:
    if state is None:
        return WatchStateResponse()
    return WatchStateResponse(
        watched=state.played,
        position_ms=state.position_ms,
        favorite=state.favorite,
        rating=state.rating,
        play_count=state.play_count,
        last_played_at=state.last_played_at,
    )


def subtitle_responses(
    file_id: str, video_path: str, *, token: str | None = None
) -> list[SubtitleResponse]:
    """Describe the sidecars next to a file, optionally with a media token."""
    query = f"?token={quote(token, safe='')}" if token else ""
    responses: list[SubtitleResponse] = []
    for path in discover_subtitles(video_path):
        subtitle_id = encode_subtitle_id(file_id, path.name)
        responses.append(
            SubtitleResponse(
                id=subtitle_id,
                name=path.name,
                format=path.suffix.casefold().lstrip("."),
                language=subtitle_language(video_path, path),
                url=f"/api/v1/subtitles/{subtitle_id}.vtt{query}",
            )
        )
    return responses


def _ordered_images(item: MediaItem) -> list[ImageModel]:
    return sorted(item.images, key=lambda image: (image.kind, not image.is_primary, image.id))


def _image_response(image: ImageModel) -> ImageResponse:
    return ImageResponse(
        id=image.id,
        kind=image.kind,
        width=image.width,
        height=image.height,
        blurhash=image.blurhash,
        is_primary=image.is_primary,
        url=f"/api/v1/images/{image.id}",
    )


def _primary_image(item: MediaItem, kind: str) -> ImageResponse | None:
    candidates = [image for image in item.images if image.kind == kind]
    if candidates:
        primary = next((image for image in candidates if image.is_primary), candidates[0])
        return _image_response(primary)
    if item.type == "episode" and kind == "poster":
        stills = [image for image in item.images if image.kind in ("still", "thumbnail")]
        if stills:
            return _image_response(stills[0])
    return None


def item_summary(
    item: MediaItem,
    state: UserItemState | None,
    episode_count: int | None = None,
) -> ItemSummary:
    series_id: str | None = None
    series_title: str | None = None
    series_poster: ImageResponse | None = None
    season_number: int | None = None
    episode_number: int | None = None
    volume: str | None = None
    if item.type == "episode":
        ep = getattr(item, "episode", None)
        if ep is not None:
            series_id = ep.series_id
            season_number = ep.season_number
            episode_number = ep.episode_number
            series = getattr(ep, "series", None)
            if series is not None and getattr(series, "item", None) is not None:
                series_title = series.item.title
                series_poster = _primary_image(series.item, "poster")
    elif item.type == "book":
        book = getattr(item, "book", None)
        if book is not None and book.series_index is not None:
            idx = book.series_index
            volume = f"Vol. {int(idx)}" if idx == int(idx) else f"Vol. {idx}"

    return ItemSummary(
        id=item.id,
        type=item.type,
        title=item.title,
        sort_title=item.sort_title,
        year=item.year,
        overview=item.overview,
        rating=item.rating,
        content_rating=item.content_rating,
        runtime_seconds=item.runtime_seconds,
        added_at=item.added_at,
        match_state=item.match_state,
        genres=sorted(genre.name for genre in item.genres),
        poster=_primary_image(item, "poster"),
        backdrop=_primary_image(item, "backdrop"),
        watch_state=watch_state_response(state),
        series_id=series_id,
        series_title=series_title,
        series_poster=series_poster,
        season_number=season_number,
        episode_number=episode_number,
        episode_count=episode_count,
        volume=volume,
    )


async def _people(session: AsyncSession, item_id: str) -> list[PersonCreditResponse]:
    rows = await session.execute(
        select(Person.name, ItemPerson.role, ItemPerson.character, ItemPerson.ordering)
        .join(ItemPerson, ItemPerson.person_id == Person.id)
        .where(ItemPerson.item_id == item_id)
        .order_by(ItemPerson.role, ItemPerson.ordering, Person.name)
    )
    return [
        PersonCreditResponse(name=name, role=role, character=character, ordering=ordering)
        for name, role, character, ordering in rows.all()
    ]


__all__ = ["ItemDetail", "ItemPage", "ItemSummary", "router"]
