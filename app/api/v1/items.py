"""Movie browse and item-detail endpoints with cursor pagination."""

import base64
import binascii
import json
from datetime import datetime
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.deps import (
    LibraryAccessDep,
    RequireUser,
    user_can_access_library,
)
from app.core.errors import NotFoundError, ValidationError
from app.db.models import (
    Genre,
    ItemPerson,
    MediaItem,
    Person,
    UserItemState,
)
from app.db.models import (
    Image as ImageModel,
)
from app.db.session import SessionDep

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


class ItemSummary(BaseModel):
    """A catalog card."""

    id: str
    title: str
    sort_title: str
    year: int | None = None
    overview: str | None = None
    rating: float | None = None
    runtime_seconds: int | None = None
    added_at: datetime
    match_state: str
    genres: list[str] = []
    poster: ImageResponse | None = None
    backdrop: ImageResponse | None = None
    watch_state: WatchStateResponse


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
) -> ItemPage:
    """List a library's movies, sorted and filtered with keyset pagination."""
    direction: SortOrder = order or _DEFAULT_ORDER[sort]
    key_expression = _sort_expression(sort, direction)

    query = select(MediaItem).where(
        MediaItem.library_id == library.id,
        MediaItem.type == "movie",
    )
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
    rows = list(await session.scalars(query.limit(limit + 1)))

    has_more = len(rows) > limit
    page_items = rows[:limit]
    states = await _watch_states(session, user.id, [item.id for item in page_items])

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
        items=[_summary(item, states.get(item.id)) for item in page_items],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@router.get("/items/{item_id}")
async def get_item(item_id: str, user: RequireUser, session: SessionDep) -> ItemDetail:
    """Return one item if the current user can access its library."""
    item = await session.get(MediaItem, item_id)
    if item is None or not await user_can_access_library(session, user, item.library_id):
        raise NotFoundError("Item not found", code="item_not_found")

    state = await session.scalar(
        select(UserItemState).where(
            UserItemState.user_id == user.id,
            UserItemState.item_id == item.id,
        )
    )
    people = await _people(session, item.id)
    summary = _summary(item, state)
    movie = item.movie
    return ItemDetail(
        **summary.model_dump(),
        original_title=movie.original_title if movie else None,
        tagline=movie.tagline if movie else None,
        release_date=movie.release_date if movie else None,
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
            )
            for media_file in sorted(item.files, key=lambda file: file.path)
        ],
        people=people,
        images=[_image_response(image) for image in _ordered_images(item)],
    )


# ------------------------------------------------------------------- helpers


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


async def _watch_states(
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


def _watch_state(state: UserItemState | None) -> WatchStateResponse:
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
    if not candidates:
        return None
    primary = next((image for image in candidates if image.is_primary), candidates[0])
    return _image_response(primary)


def _summary(item: MediaItem, state: UserItemState | None) -> ItemSummary:
    return ItemSummary(
        id=item.id,
        title=item.title,
        sort_title=item.sort_title,
        year=item.year,
        overview=item.overview,
        rating=item.rating,
        runtime_seconds=item.runtime_seconds,
        added_at=item.added_at,
        match_state=item.match_state,
        genres=sorted(genre.name for genre in item.genres),
        poster=_primary_image(item, "poster"),
        backdrop=_primary_image(item, "backdrop"),
        watch_state=_watch_state(state),
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
