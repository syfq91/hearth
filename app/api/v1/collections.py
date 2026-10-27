"""Admin-curated collections endpoints (e.g. franchise groupings)."""

from datetime import datetime

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.api.v1.items import ItemSummary, item_summary, watch_states
from app.core.deps import RequireAdmin, RequireUser
from app.core.errors import NotFoundError
from app.db.base import utcnow
from app.db.models import Collection, CollectionItem, LibraryAccess, MediaItem
from app.db.session import SessionDep

router = APIRouter(prefix="/collections", tags=["collections"])


class CollectionSummary(BaseModel):
    """Collection overview without child items."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    overview: str | None = None
    poster_path: str | None = None
    tmdb_id: int | None = None
    item_count: int = 0
    created_at: datetime
    updated_at: datetime


class CollectionDetail(BaseModel):
    """Collection with ordered media items."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    overview: str | None = None
    poster_path: str | None = None
    tmdb_id: int | None = None
    items: list[ItemSummary] = []
    created_at: datetime
    updated_at: datetime


class CreateCollectionRequest(BaseModel):
    """Create a new collection."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    overview: str | None = None
    poster_path: str | None = Field(default=None, max_length=512)
    tmdb_id: int | None = None
    item_ids: list[str] = Field(default_factory=list)


class UpdateCollectionRequest(BaseModel):
    """Update collection metadata."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    overview: str | None = None
    poster_path: str | None = Field(default=None, max_length=512)
    tmdb_id: int | None = None


class AddCollectionItemsRequest(BaseModel):
    """Add items to a collection."""

    model_config = ConfigDict(extra="forbid")

    item_ids: list[str] = Field(min_length=1)


class ReorderCollectionItemsRequest(BaseModel):
    """Reorder items in a collection."""

    model_config = ConfigDict(extra="forbid")

    item_ids: list[str] = Field(min_length=1)


@router.get("", response_model=list[CollectionSummary])
async def list_collections(
    user: RequireUser,
    session: SessionDep,
) -> list[CollectionSummary]:
    """List all curated collections with item counts."""
    query = (
        select(
            Collection,
            func.count(CollectionItem.id).label("item_count"),
        )
        .outerjoin(CollectionItem, CollectionItem.collection_id == Collection.id)
        .group_by(Collection.id)
        .order_by(Collection.name.asc())
    )
    rows = (await session.execute(query)).all()
    return [
        CollectionSummary(
            id=col.id,
            name=col.name,
            overview=col.overview,
            poster_path=col.poster_path,
            tmdb_id=col.tmdb_id,
            item_count=count,
            created_at=col.created_at,
            updated_at=col.updated_at,
        )
        for col, count in rows
    ]


@router.post("", response_model=CollectionDetail, status_code=status.HTTP_201_CREATED)
async def create_collection(
    body: CreateCollectionRequest,
    admin: RequireAdmin,
    session: SessionDep,
) -> CollectionDetail:
    """Create a new collection (admin only).

    If tmdb_id is supplied and item_ids is empty, any existing movies
    in the database whose provider_ids contain matching tmdb_collection_id
    will automatically be included.
    """
    now = utcnow()
    collection = Collection(
        name=body.name.strip(),
        overview=body.overview,
        poster_path=body.poster_path,
        tmdb_id=body.tmdb_id,
        created_at=now,
        updated_at=now,
    )
    session.add(collection)
    await session.flush()

    item_ids_to_add = list(body.item_ids)

    # Auto-collect by TMDB collection ID if provided and item_ids empty
    if not item_ids_to_add and body.tmdb_id is not None:
        all_movies = await session.scalars(select(MediaItem).where(MediaItem.type == "movie"))
        for m in all_movies.all():
            p_ids = m.provider_ids or {}
            c_id = p_ids.get("tmdb_collection_id") or p_ids.get("collection_id")
            if c_id is not None and str(c_id) == str(body.tmdb_id):
                item_ids_to_add.append(m.id)

    # Add items
    for pos, iid in enumerate(item_ids_to_add):
        session.add(
            CollectionItem(
                collection_id=collection.id,
                media_item_id=iid,
                position=pos,
                added_at=now,
            )
        )

    await session.commit()
    return await _get_collection_detail(session, admin, collection.id)


@router.get("/{collection_id}", response_model=CollectionDetail)
async def get_collection(
    collection_id: str,
    user: RequireUser,
    session: SessionDep,
) -> CollectionDetail:
    """Get a collection with its items (respecting library access)."""
    return await _get_collection_detail(session, user, collection_id)


@router.patch("/{collection_id}", response_model=CollectionDetail)
async def update_collection(
    collection_id: str,
    body: UpdateCollectionRequest,
    admin: RequireAdmin,
    session: SessionDep,
) -> CollectionDetail:
    """Update collection metadata (admin only)."""
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise NotFoundError("Collection not found", code="collection_not_found")

    if body.name is not None:
        collection.name = body.name.strip()
    if body.overview is not None:
        collection.overview = body.overview
    if body.poster_path is not None:
        collection.poster_path = body.poster_path
    if body.tmdb_id is not None:
        collection.tmdb_id = body.tmdb_id
    collection.updated_at = utcnow()

    await session.commit()
    return await _get_collection_detail(session, admin, collection.id)


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_collection(
    collection_id: str,
    admin: RequireAdmin,
    session: SessionDep,
) -> Response:
    """Delete a collection (admin only)."""
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise NotFoundError("Collection not found", code="collection_not_found")

    await session.delete(collection)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{collection_id}/items", response_model=CollectionDetail)
async def add_collection_items(
    collection_id: str,
    body: AddCollectionItemsRequest,
    admin: RequireAdmin,
    session: SessionDep,
) -> CollectionDetail:
    """Add items to a collection (admin only)."""
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise NotFoundError("Collection not found", code="collection_not_found")

    # Get max current position
    max_pos = (
        await session.scalar(
            select(func.max(CollectionItem.position)).where(
                CollectionItem.collection_id == collection.id
            )
        )
        or -1
    )

    existing_item_ids = set(
        await session.scalars(
            select(CollectionItem.media_item_id).where(
                CollectionItem.collection_id == collection.id
            )
        )
    )

    now = utcnow()
    next_pos = max_pos + 1
    for iid in body.item_ids:
        if iid not in existing_item_ids:
            session.add(
                CollectionItem(
                    collection_id=collection.id,
                    media_item_id=iid,
                    position=next_pos,
                    added_at=now,
                )
            )
            next_pos += 1
            existing_item_ids.add(iid)

    collection.updated_at = now
    await session.commit()
    return await _get_collection_detail(session, admin, collection.id)


@router.delete("/{collection_id}/items/{item_id}", response_model=CollectionDetail)
async def remove_collection_item(
    collection_id: str,
    item_id: str,
    admin: RequireAdmin,
    session: SessionDep,
) -> CollectionDetail:
    """Remove an item from a collection (admin only)."""
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise NotFoundError("Collection not found", code="collection_not_found")

    ci = await session.scalar(
        select(CollectionItem).where(
            CollectionItem.collection_id == collection.id,
            CollectionItem.media_item_id == item_id,
        )
    )
    if ci is not None:
        await session.delete(ci)
        collection.updated_at = utcnow()
        await session.commit()

    return await _get_collection_detail(session, admin, collection.id)


@router.put("/{collection_id}/items/order", response_model=CollectionDetail)
async def reorder_collection_items(
    collection_id: str,
    body: ReorderCollectionItemsRequest,
    admin: RequireAdmin,
    session: SessionDep,
) -> CollectionDetail:
    """Reorder items in a collection (admin only)."""
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise NotFoundError("Collection not found", code="collection_not_found")

    items = list(
        (
            await session.scalars(
                select(CollectionItem).where(CollectionItem.collection_id == collection.id)
            )
        ).all()
    )
    item_map = {ci.media_item_id: ci for ci in items}

    # First pass: shift to negative offsets to avoid unique collisions
    for idx, iid in enumerate(body.item_ids):
        if iid in item_map:
            item_map[iid].position = -(idx + 1000)
    await session.flush()

    # Second pass: set final zero-based positions
    for pos, iid in enumerate(body.item_ids):
        if iid in item_map:
            item_map[iid].position = pos

    collection.updated_at = utcnow()
    await session.commit()
    return await _get_collection_detail(session, admin, collection.id)


async def _get_collection_detail(
    session: SessionDep,
    user: RequireUser,
    collection_id: str,
) -> CollectionDetail:
    collection = await session.get(Collection, collection_id)
    if collection is None:
        raise NotFoundError("Collection not found", code="collection_not_found")

    # Load collection items ordered by position
    cis = list(
        (
            await session.scalars(
                select(CollectionItem)
                .where(CollectionItem.collection_id == collection.id)
                .order_by(CollectionItem.position.asc())
            )
        ).all()
    )

    item_ids = [ci.media_item_id for ci in cis]
    if not item_ids:
        return CollectionDetail(
            id=collection.id,
            name=collection.name,
            overview=collection.overview,
            poster_path=collection.poster_path,
            tmdb_id=collection.tmdb_id,
            items=[],
            created_at=collection.created_at,
            updated_at=collection.updated_at,
        )

    # Filter by user library access
    media_query = select(MediaItem).where(MediaItem.id.in_(item_ids))
    if user.role != "admin":
        media_query = media_query.join(
            LibraryAccess,
            (LibraryAccess.library_id == MediaItem.library_id) & (LibraryAccess.user_id == user.id),
        )

    accessible_items = {item.id: item for item in (await session.scalars(media_query)).all()}
    ordered_items = [accessible_items[iid] for iid in item_ids if iid in accessible_items]
    states = await watch_states(session, user.id, [i.id for i in ordered_items])

    summaries = [item_summary(i, states.get(i.id)) for i in ordered_items]

    return CollectionDetail(
        id=collection.id,
        name=collection.name,
        overview=collection.overview,
        poster_path=collection.poster_path,
        tmdb_id=collection.tmdb_id,
        items=summaries,
        created_at=collection.created_at,
        updated_at=collection.updated_at,
    )
