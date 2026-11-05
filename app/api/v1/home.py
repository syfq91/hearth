"""Home shelves: continue watching, continue reading, recently added and favorites."""

from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.api.v1.books import BookListItem, _to_book_list_item
from app.api.v1.items import ItemSummary, item_summary, watch_states
from app.core.deps import RequireUser, require_library_access
from app.db.models import (
    Book,
    BookProgress,
    LibraryAccess,
    MediaItem,
    User,
    UserItemState,
)
from app.db.session import SessionDep

router = APIRouter(tags=["home"])

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class HomeShelf(BaseModel):
    """A short, ordered list of items for a home row."""

    items: list[ItemSummary]
    has_more: bool = False


class BookShelf(BaseModel):
    """A short, ordered list of books for a home row."""

    items: list[BookListItem]
    has_more: bool = False


@router.get("/home/continue-watching")
async def continue_watching(
    user: RequireUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> HomeShelf:
    """Return the current user's unfinished items, most recently played first."""
    query = (
        select(MediaItem, UserItemState)
        .join(UserItemState, UserItemState.item_id == MediaItem.id)
        .where(
            UserItemState.user_id == user.id,
            UserItemState.played.is_(False),
            UserItemState.position_ms > 0,
            UserItemState.last_played_at.is_not(None),
        )
        .order_by(UserItemState.last_played_at.desc(), MediaItem.id.desc())
        .options(selectinload(MediaItem.images), selectinload(MediaItem.genres))
    )
    query = _restrict_to_accessible_libraries(query, user)
    rows = (await session.execute(query.limit(limit + 1))).all()
    return HomeShelf(
        items=[item_summary(item, state) for item, state in rows[:limit]],
        has_more=len(rows) > limit,
    )


@router.get("/home/continue-reading")
async def continue_reading(
    user: RequireUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> BookShelf:
    """Return the current user's unfinished books, most recently read first."""
    query = (
        select(MediaItem, BookProgress)
        .join(BookProgress, BookProgress.book_id == MediaItem.id)
        .where(
            BookProgress.user_id == user.id,
            BookProgress.percentage > 0,
            BookProgress.completed.is_(False),
            MediaItem.type == "book",
        )
        .order_by(BookProgress.updated_at.desc(), MediaItem.id.desc())
        .options(
            selectinload(MediaItem.book).selectinload(Book.author),
            selectinload(MediaItem.book).selectinload(Book.series),
            selectinload(MediaItem.files),
            selectinload(MediaItem.images),
        )
    )
    query = _restrict_to_accessible_libraries(query, user)
    rows = (await session.execute(query.limit(limit + 1))).all()
    return BookShelf(
        items=[_to_book_list_item(item, progress) for item, progress in rows[:limit]],
        has_more=len(rows) > limit,
    )


@router.get("/home/recently-added")
async def recently_added(
    user: RequireUser,
    session: SessionDep,
    library_id: Annotated[str | None, Query(max_length=36)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> HomeShelf:
    """Return recently added items across accessible libraries or one library."""
    if library_id is not None:
        await require_library_access(library_id, user, session)

    query = (
        select(MediaItem)
        .order_by(MediaItem.added_at.desc(), MediaItem.id.desc())
        .options(selectinload(MediaItem.images), selectinload(MediaItem.genres))
    )
    if library_id is not None:
        query = query.where(MediaItem.library_id == library_id)
    else:
        query = _restrict_to_accessible_libraries(query, user)
    rows = list(await session.scalars(query.limit(limit + 1)))
    page = rows[:limit]
    states = await watch_states(session, user.id, [item.id for item in page])
    return HomeShelf(
        items=[item_summary(item, states.get(item.id)) for item in page],
        has_more=len(rows) > limit,
    )


@router.get("/home/favorites")
async def favorites(
    user: RequireUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> HomeShelf:
    """Return the current user's favorites, most recently changed first."""
    query = (
        select(MediaItem, UserItemState)
        .join(UserItemState, UserItemState.item_id == MediaItem.id)
        .where(
            UserItemState.user_id == user.id,
            UserItemState.favorite.is_(True),
        )
        .order_by(UserItemState.updated_at.desc(), MediaItem.id.desc())
        .options(selectinload(MediaItem.images), selectinload(MediaItem.genres))
    )
    query = _restrict_to_accessible_libraries(query, user)
    rows = (await session.execute(query.limit(limit + 1))).all()
    return HomeShelf(
        items=[item_summary(item, state) for item, state in rows[:limit]],
        has_more=len(rows) > limit,
    )


def _restrict_to_accessible_libraries(query: Any, user: User) -> Any:
    """Apply the ADR 015 library rule to any ``MediaItem`` query."""
    if user.role == "admin":
        return query
    grant = select(LibraryAccess.library_id).where(LibraryAccess.user_id == user.id)
    return query.where(MediaItem.library_id.in_(grant))


__all__ = ["BookShelf", "HomeShelf", "router"]
