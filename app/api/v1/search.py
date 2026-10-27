"""Unified full-text search across all media types and people using SQLite FTS5."""

import re
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import bindparam, select, text

from app.api.v1.items import ItemSummary, item_summary, watch_states
from app.core.deps import RequireUser
from app.db.models import ItemPerson, LibraryAccess, MediaItem, Person, UserItemState
from app.db.session import SessionDep
from app.services.ratings import is_rating_permitted

router = APIRouter(tags=["search"])

SearchType = Literal["all", "movie", "series", "episode", "album", "track", "book", "person"]


class SearchPersonResult(BaseModel):
    """A person credit match (cast, crew, artist)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class SearchResponse(BaseModel):
    """Grouped search results partitioned by media type and people."""

    model_config = ConfigDict(extra="forbid")

    movies: list[ItemSummary] = []
    series: list[ItemSummary] = []
    episodes: list[ItemSummary] = []
    albums: list[ItemSummary] = []
    tracks: list[ItemSummary] = []
    books: list[ItemSummary] = []
    people: list[SearchPersonResult] = []
    total: int = 0


def _build_fts_query(term: str) -> str | None:
    """Sanitize and format query string into FTS5 prefix syntax.

    Tokens are wrapped in double quotes to prevent FTS5 operator injection,
    and the final token receives an asterisk (*) for live search prefix matching.
    """
    tokens = re.findall(r"\w+", term, re.UNICODE)
    if not tokens:
        return None
    # All leading tokens quoted, last token quoted with prefix wildcard
    parts = [f'"{t}"' for t in tokens[:-1]]
    parts.append(f'"{tokens[-1]}"*')
    return " ".join(parts)


@router.get("/search", response_model=SearchResponse)
async def search(
    q: Annotated[str, Query(description="Search query string", max_length=200)],
    user: RequireUser,
    session: SessionDep,
    type: Annotated[SearchType, Query(description="Filter by media type")] = "all",
    limit_per_type: Annotated[
        int, Query(description="Maximum results per media category", ge=1, le=50)
    ] = 10,
) -> SearchResponse:
    """Unified search across all accessible media libraries and credits."""
    clean_q = q.strip()
    if not clean_q:
        return SearchResponse()

    fts_expression = _build_fts_query(clean_q)
    if not fts_expression:
        return SearchResponse()

    # Determine user-accessible libraries
    library_ids: list[str] | None = None
    if user.role != "admin":
        accessible = await session.scalars(
            select(LibraryAccess.library_id).where(LibraryAccess.user_id == user.id)
        )
        library_ids = list(accessible.all())
        if not library_ids:
            return SearchResponse()

    tokens = re.findall(r"\w+", clean_q, re.UNICODE)

    # 1. Search media items via FTS5 if type allows
    item_rows: list[tuple[str, str, float]] = []
    if type != "person":
        sql_parts = [
            "WITH matches AS (",
            "    SELECT fts.rowid, bm25(media_items_fts) AS rank",
            "    FROM media_items_fts fts",
            "    WHERE media_items_fts MATCH :query",
            "),",
            "ranked AS (",
            "    SELECT m.id, m.type, matches.rank,",
            "           ROW_NUMBER() OVER (PARTITION BY m.type ORDER BY matches.rank ASC) as rn",
            "    FROM matches",
            "    JOIN media_items m ON matches.rowid = m.rowid",
            "    WHERE 1=1",
        ]
        params: dict[str, object] = {"query": fts_expression, "limit": limit_per_type}

        if library_ids is not None:
            sql_parts.append("      AND m.library_id IN :lib_ids")
            params["lib_ids"] = tuple(library_ids)

        if type != "all":
            sql_parts.append("      AND m.type = :target_type")
            params["target_type"] = type

        sql_parts.extend(
            [
                ")",
                "SELECT id, type, rank FROM ranked WHERE rn <= :limit ORDER BY rank ASC",
            ]
        )

        statement = text("\n".join(sql_parts))
        if library_ids is not None:
            statement = statement.bindparams(bindparam("lib_ids", expanding=True))

        result = await session.execute(statement, params)
        item_rows = [(row[0], row[1], float(row[2])) for row in result.fetchall()]

    # Load media items and watch states
    item_ids = [row[0] for row in item_rows]
    items_by_id: dict[str, MediaItem] = {}
    states: dict[str, UserItemState] = {}
    if item_ids:
        loaded_items = await session.scalars(select(MediaItem).where(MediaItem.id.in_(item_ids)))
        items_by_id = {item.id: item for item in loaded_items.all()}
        states = await watch_states(session, user.id, list(items_by_id.keys()))

    movies: list[ItemSummary] = []
    series: list[ItemSummary] = []
    episodes: list[ItemSummary] = []
    albums: list[ItemSummary] = []
    tracks: list[ItemSummary] = []
    books: list[ItemSummary] = []

    for row_id, item_type, _ in item_rows:
        item = items_by_id.get(row_id)
        if item is None or not is_rating_permitted(item.content_rating, user.max_rating):
            continue
        summary = item_summary(item, states.get(item.id))
        if item_type == "movie":
            movies.append(summary)
        elif item_type == "series":
            series.append(summary)
        elif item_type == "episode":
            episodes.append(summary)
        elif item_type == "album":
            albums.append(summary)
        elif item_type == "track":
            tracks.append(summary)
        elif item_type == "book":
            books.append(summary)

    # 2. Search people credits
    people: list[SearchPersonResult] = []
    if type in ("all", "person") and tokens:
        person_query = (
            select(Person.id, Person.name)
            .distinct()
            .join(ItemPerson, ItemPerson.person_id == Person.id)
            .join(MediaItem, MediaItem.id == ItemPerson.item_id)
        )
        if library_ids is not None:
            person_query = person_query.where(MediaItem.library_id.in_(library_ids))

        for t in tokens:
            person_query = person_query.where(Person.name.ilike(f"%{t}%"))

        person_query = person_query.order_by(Person.name.asc()).limit(limit_per_type)
        person_rows = (await session.execute(person_query)).fetchall()
        people = [SearchPersonResult(id=r[0], name=r[1]) for r in person_rows]

    total = (
        len(movies)
        + len(series)
        + len(episodes)
        + len(albums)
        + len(tracks)
        + len(books)
        + len(people)
    )

    return SearchResponse(
        movies=movies,
        series=series,
        episodes=episodes,
        albums=albums,
        tracks=tracks,
        books=books,
        people=people,
        total=total,
    )
