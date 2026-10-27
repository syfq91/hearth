"""Book and comic browsing, search, download, and reading progress endpoints."""

from __future__ import annotations

import asyncio
import io
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response
from PIL import Image as PILImage
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.api.v1.stream import stream_media_file
from app.core.deps import RequireUser, SettingsDep, ViewerDep, user_can_access_library
from app.core.errors import NotFoundError, ValidationError
from app.db.base import utcnow
from app.db.models import (
    Author,
    Book,
    BookProgress,
    BookSeries,
    Library,
    LibraryAccess,
    MediaFile,
    MediaItem,
    User,
)
from app.db.session import SessionDep
from app.services.media.books import extract_comic_page

router = APIRouter(tags=["books"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def _resize_image_page(raw: bytes, max_width: int) -> tuple[bytes, str]:
    with PILImage.open(io.BytesIO(raw)) as img:
        output: PILImage.Image = img
        if img.width > max_width:
            ratio = max_width / img.width
            new_h = max(1, round(img.height * ratio))
            output = img.resize((max_width, new_h), PILImage.Resampling.LANCZOS)
        buf = io.BytesIO()
        output.convert("RGB").save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue(), "image/jpeg"


# ------------------------------------------------------------- helper queries


def _restrict_to_accessible_libraries(query: Any, user: User) -> Any:
    """Filter MediaItem query by the libraries the user has access to."""
    if user.role == "admin":
        return query
    grant = select(LibraryAccess.library_id).where(LibraryAccess.user_id == user.id)
    return query.where(MediaItem.library_id.in_(grant))


def _primary_image_id(item: MediaItem) -> str | None:
    for img in item.images:
        if img.kind == "poster" and img.is_primary:
            return img.id
    for img in item.images:
        if img.kind == "poster":
            return img.id
    return item.images[0].id if item.images else None


# ------------------------------------------------------------- response schemas


class AuthorBrief(BaseModel):
    id: str
    name: str


class SeriesBrief(BaseModel):
    id: str
    name: str
    index: float | None = None


class BookProgressResponse(BaseModel):
    book_id: str
    locator: str
    percentage: float
    device_name: str | None = None
    completed: bool
    updated_at: datetime


class BookProgressRequest(BaseModel):
    locator: str = Field(..., max_length=1000)
    percentage: float = Field(..., ge=0.0, le=100.0)
    device_name: str | None = Field(default=None, max_length=100)
    completed: bool | None = None


class BookFileResponse(BaseModel):
    id: str
    container: str
    size_bytes: int | None = None
    mtime: float | None = None


class BookListItem(BaseModel):
    id: str
    library_id: str
    title: str
    sort_title: str | None = None
    year: int | None = None
    overview: str | None = None
    isbn: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    page_count: int | None = None
    language: str | None = None
    author: AuthorBrief | None = None
    series: SeriesBrief | None = None
    formats: list[str] = []
    cover_image_id: str | None = None
    progress: BookProgressResponse | None = None


class BookListResponse(BaseModel):
    items: list[BookListItem]
    total: int
    limit: int
    offset: int


class BookDetail(BaseModel):
    id: str
    library_id: str
    title: str
    sort_title: str | None = None
    year: int | None = None
    overview: str | None = None
    isbn: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    page_count: int | None = None
    language: str | None = None
    author: AuthorBrief | None = None
    series: SeriesBrief | None = None
    formats: list[str] = []
    files: list[BookFileResponse] = []
    cover_image_id: str | None = None
    progress: BookProgressResponse | None = None
    created_at: datetime


class AuthorListItem(BaseModel):
    id: str
    name: str
    sort_name: str | None = None
    book_count: int = 0


class AuthorListResponse(BaseModel):
    items: list[AuthorListItem]
    total: int
    limit: int
    offset: int


class AuthorDetail(BaseModel):
    id: str
    name: str
    sort_name: str | None = None
    overview: str | None = None
    books: list[BookListItem] = []


class BookSeriesListItem(BaseModel):
    id: str
    name: str
    book_count: int = 0


class BookSeriesListResponse(BaseModel):
    items: list[BookSeriesListItem]
    total: int
    limit: int
    offset: int


class BookSeriesDetail(BaseModel):
    id: str
    name: str
    description: str | None = None
    books: list[BookListItem] = []


class BookSearchResponse(BaseModel):
    items: list[BookListItem]
    total: int


# ------------------------------------------------------------- conversion helpers


def _to_book_list_item(item: MediaItem, progress: BookProgress | None = None) -> BookListItem:
    book = item.book
    author_brief = (
        AuthorBrief(id=book.author.id, name=book.author.name) if book and book.author else None
    )
    series_brief = (
        SeriesBrief(
            id=book.series.id,
            name=book.series.name,
            index=book.series_index,
        )
        if book and book.series
        else None
    )
    formats = sorted({f.container for f in item.files if f.container and f.missing_since is None})
    prog_resp = (
        BookProgressResponse(
            book_id=progress.book_id,
            locator=progress.locator,
            percentage=progress.percentage,
            device_name=progress.device_name,
            completed=progress.completed,
            updated_at=progress.updated_at,
        )
        if progress
        else None
    )
    return BookListItem(
        id=item.id,
        library_id=item.library_id,
        title=item.title,
        sort_title=item.sort_title,
        year=item.year,
        overview=item.overview,
        isbn=book.isbn if book else None,
        publisher=book.publisher if book else None,
        published_date=book.published_date if book else None,
        page_count=book.page_count if book else None,
        language=book.language if book else None,
        author=author_brief,
        series=series_brief,
        formats=formats,
        cover_image_id=_primary_image_id(item),
        progress=prog_resp,
    )


# ------------------------------------------------------------- endpoints


@router.get("/books", response_model=BookListResponse)
async def list_books(
    user: RequireUser,
    session: SessionDep,
    library_id: Annotated[str | None, Query()] = None,
    author_id: Annotated[str | None, Query()] = None,
    series_id: Annotated[str | None, Query()] = None,
    sort: Annotated[
        str,
        Query(description="Sort field: title, -title, author, -author, date, -date, added, -added"),
    ] = "title",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BookListResponse:
    """List catalogued books with optional filtering, sorting, and pagination."""
    if library_id:
        lib = await session.get(Library, library_id)
        if lib is None or not await user_can_access_library(session, user, library_id):
            raise NotFoundError("Library not found", code="library_not_found")

    q = (
        select(MediaItem)
        .join(Book, Book.item_id == MediaItem.id)
        .options(
            selectinload(MediaItem.book).selectinload(Book.author),
            selectinload(MediaItem.book).selectinload(Book.series),
            selectinload(MediaItem.files),
            selectinload(MediaItem.images),
        )
        .where(MediaItem.type == "book")
    )
    q = _restrict_to_accessible_libraries(q, user)

    if library_id:
        q = q.where(MediaItem.library_id == library_id)
    if author_id:
        q = q.where(Book.author_id == author_id)
    if series_id:
        q = q.where(Book.series_id == series_id)

    # Count total
    count_q = (
        select(func.count(MediaItem.id))
        .join(Book, Book.item_id == MediaItem.id)
        .where(MediaItem.type == "book")
    )
    count_q = _restrict_to_accessible_libraries(count_q, user)
    if library_id:
        count_q = count_q.where(MediaItem.library_id == library_id)
    if author_id:
        count_q = count_q.where(Book.author_id == author_id)
    if series_id:
        count_q = count_q.where(Book.series_id == series_id)
    total = (await session.scalar(count_q)) or 0

    # Sorting
    if sort == "-title":
        q = q.order_by(MediaItem.sort_title.desc().nullslast())
    elif sort == "author":
        q = q.outerjoin(Author, Book.author_id == Author.id).order_by(
            Author.sort_name.asc().nullslast(), MediaItem.sort_title.asc()
        )
    elif sort == "-author":
        q = q.outerjoin(Author, Book.author_id == Author.id).order_by(
            Author.sort_name.desc().nullslast(), MediaItem.sort_title.asc()
        )
    elif sort == "date":
        q = q.order_by(Book.published_date.asc().nullslast(), MediaItem.sort_title.asc())
    elif sort == "-date":
        q = q.order_by(Book.published_date.desc().nullslast(), MediaItem.sort_title.asc())
    elif sort == "added":
        q = q.order_by(MediaItem.added_at.asc())
    elif sort == "-added":
        q = q.order_by(MediaItem.added_at.desc())
    else:
        q = q.order_by(MediaItem.sort_title.asc())

    q = q.offset(offset).limit(limit)
    items = list(await session.scalars(q))

    # Fetch progress for these books
    item_ids = [it.id for it in items]
    prog_map: dict[str, BookProgress] = {}
    if item_ids:
        progs = await session.scalars(
            select(BookProgress).where(
                BookProgress.user_id == user.id,
                BookProgress.book_id.in_(item_ids),
            )
        )
        prog_map = {p.book_id: p for p in progs}

    return BookListResponse(
        items=[_to_book_list_item(it, prog_map.get(it.id)) for it in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/books/search", response_model=BookSearchResponse)
async def search_books(
    user: RequireUser,
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=255)] = None,
    query: Annotated[str | None, Query(max_length=255)] = None,
    library_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> BookSearchResponse:
    """Search books by title, author name, ISBN, or series name."""
    term = (q or query or "").strip()
    if not term:
        raise ValidationError("Search query cannot be empty", code="query_required")
    pattern = f"%{term}%"

    if library_id:
        lib = await session.get(Library, library_id)
        if lib is None or not await user_can_access_library(session, user, library_id):
            raise NotFoundError("Library not found", code="library_not_found")

    search_q = (
        select(MediaItem)
        .join(Book, Book.item_id == MediaItem.id)
        .outerjoin(Author, Book.author_id == Author.id)
        .outerjoin(BookSeries, Book.series_id == BookSeries.id)
        .options(
            selectinload(MediaItem.book).selectinload(Book.author),
            selectinload(MediaItem.book).selectinload(Book.series),
            selectinload(MediaItem.files),
            selectinload(MediaItem.images),
        )
        .where(
            MediaItem.type == "book",
            or_(
                MediaItem.title.ilike(pattern),
                Author.name.ilike(pattern),
                Book.isbn.ilike(pattern),
                BookSeries.name.ilike(pattern),
            ),
        )
    )
    search_q = _restrict_to_accessible_libraries(search_q, user)
    if library_id:
        search_q = search_q.where(MediaItem.library_id == library_id)

    search_q = search_q.order_by(MediaItem.sort_title.asc()).limit(limit)
    items = list(await session.scalars(search_q))

    item_ids = [it.id for it in items]
    prog_map: dict[str, BookProgress] = {}
    if item_ids:
        progs = await session.scalars(
            select(BookProgress).where(
                BookProgress.user_id == user.id,
                BookProgress.book_id.in_(item_ids),
            )
        )
        prog_map = {p.book_id: p for p in progs}

    return BookSearchResponse(
        items=[_to_book_list_item(it, prog_map.get(it.id)) for it in items],
        total=len(items),
    )


@router.get("/books/{book_id}", response_model=BookDetail)
async def get_book_detail(
    book_id: str,
    user: RequireUser,
    session: SessionDep,
) -> BookDetail:
    """Get full details of a single book including files and reading progress."""
    q = (
        select(MediaItem)
        .options(
            selectinload(MediaItem.book).selectinload(Book.author),
            selectinload(MediaItem.book).selectinload(Book.series),
            selectinload(MediaItem.files),
            selectinload(MediaItem.images),
        )
        .where(MediaItem.id == book_id, MediaItem.type == "book")
    )
    q = _restrict_to_accessible_libraries(q, user)
    item = await session.scalar(q)
    if item is None:
        raise NotFoundError("Book not found", code="book_not_found")

    book = item.book
    author_brief = (
        AuthorBrief(id=book.author.id, name=book.author.name) if book and book.author else None
    )
    series_brief = (
        SeriesBrief(
            id=book.series.id,
            name=book.series.name,
            index=book.series_index,
        )
        if book and book.series
        else None
    )
    active_files = [f for f in item.files if f.missing_since is None]
    formats = sorted({f.container for f in active_files if f.container})
    files_resp = [
        BookFileResponse(
            id=f.id,
            container=f.container or "unknown",
            size_bytes=f.size_bytes,
            mtime=f.mtime,
        )
        for f in active_files
    ]

    progress = await session.get(BookProgress, (user.id, item.id))
    prog_resp = (
        BookProgressResponse(
            book_id=progress.book_id,
            locator=progress.locator,
            percentage=progress.percentage,
            device_name=progress.device_name,
            completed=progress.completed,
            updated_at=progress.updated_at,
        )
        if progress
        else None
    )

    return BookDetail(
        id=item.id,
        library_id=item.library_id,
        title=item.title,
        sort_title=item.sort_title,
        year=item.year,
        overview=item.overview,
        isbn=book.isbn if book else None,
        publisher=book.publisher if book else None,
        published_date=book.published_date if book else None,
        page_count=book.page_count if book else None,
        language=book.language if book else None,
        author=author_brief,
        series=series_brief,
        formats=formats,
        files=files_resp,
        cover_image_id=_primary_image_id(item),
        progress=prog_resp,
        created_at=item.created_at,
    )


@router.get("/books/{book_id}/file/{format}")
async def download_book_file(
    book_id: str,
    format: str,
    request: Request,
    viewer: ViewerDep,
    settings: SettingsDep,
    session: SessionDep,
) -> Any:
    """Download or stream a book format with HTTP Range and Content-Disposition."""
    q = select(MediaItem).where(MediaItem.id == book_id, MediaItem.type == "book")
    q = _restrict_to_accessible_libraries(q, viewer.user)
    item = await session.scalar(q)
    if item is None:
        raise NotFoundError("Book not found", code="book_not_found")

    # Find the matching format file
    fmt = format.lower().lstrip(".")
    files = list(
        await session.scalars(
            select(MediaFile).where(
                MediaFile.item_id == book_id,
                MediaFile.container == fmt,
                MediaFile.missing_since.is_(None),
            )
        )
    )
    if not files:
        raise NotFoundError(f"Format {fmt} not available for book", code="format_not_found")

    media_file = files[0]
    filename = f"{item.title}.{fmt}"
    return await stream_media_file(
        media_file.id,
        request.method,
        request.headers,
        viewer,
        settings.stream_chunk_bytes,
        session,
        download_filename=filename,
    )


@router.get(
    "/books/{book_id}/pages/{page_number}",
    response_class=Response,
    responses={
        200: {
            "description": "Extracted comic page image",
            "content": {
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/webp": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
async def get_comic_page(
    book_id: str,
    page_number: int,
    viewer: ViewerDep,
    settings: SettingsDep,
    session: SessionDep,
    width: Annotated[int | None, Query(ge=50, le=4000)] = None,
) -> Response:
    """Extract and serve the nth (1-indexed) page from a comic with caching and resizing."""
    q = (
        select(MediaItem)
        .options(selectinload(MediaItem.files))
        .where(MediaItem.id == book_id, MediaItem.type == "book")
    )
    q = _restrict_to_accessible_libraries(q, viewer.user)
    item = await session.scalar(q)
    if item is None:
        raise NotFoundError("Book not found", code="book_not_found")

    candidate_files = [
        f for f in item.files if f.container in ("cbz", "cbr", "pdf") and f.missing_since is None
    ]
    if not candidate_files:
        raise NotFoundError("No comic file found for book", code="comic_not_found")

    media_file = candidate_files[0]
    file_path = Path(media_file.path)
    if not file_path.is_file():
        raise NotFoundError("Comic file missing on disk", code="file_not_found")

    # Cache file path
    cache_dir = settings.comic_cache_dir / book_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = (
        cache_dir / f"p{page_number}_w{width}.jpg"
        if width is not None
        else cache_dir / f"p{page_number}.bin"
    )

    if cache_file.is_file():
        cached_data = await asyncio.to_thread(cache_file.read_bytes)
        return Response(
            content=cached_data,
            media_type="image/jpeg" if width is not None else "application/octet-stream",
            headers={"Cache-Control": "private, max-age=86400", "X-Cache": "HIT"},
        )

    try:
        data, mime = await asyncio.to_thread(extract_comic_page, file_path, page_number)
    except IndexError:
        raise NotFoundError(f"Page {page_number} not found", code="page_not_found") from None
    except Exception as exc:
        raise NotFoundError(
            f"Failed to extract page: {exc}", code="page_extraction_error"
        ) from None

    if width is not None:
        data, mime = await asyncio.to_thread(_resize_image_page, data, width)

    await asyncio.to_thread(cache_file.write_bytes, data)
    return Response(
        content=data,
        media_type=mime,
        headers={"Cache-Control": "private, max-age=86400", "X-Cache": "MISS"},
    )


@router.get("/books/{book_id}/progress", response_model=BookProgressResponse)
async def get_reading_progress(
    book_id: str,
    user: RequireUser,
    session: SessionDep,
) -> BookProgressResponse:
    """Get the current user's reading progress for a book."""
    q = select(MediaItem).where(MediaItem.id == book_id, MediaItem.type == "book")
    q = _restrict_to_accessible_libraries(q, user)
    item = await session.scalar(q)
    if item is None:
        raise NotFoundError("Book not found", code="book_not_found")

    prog = await session.get(BookProgress, (user.id, book_id))
    if prog is None:
        raise NotFoundError("Reading progress not found", code="progress_not_found")

    return BookProgressResponse(
        book_id=prog.book_id,
        locator=prog.locator,
        percentage=prog.percentage,
        device_name=prog.device_name,
        completed=prog.completed,
        updated_at=prog.updated_at,
    )


@router.put("/books/{book_id}/progress", response_model=BookProgressResponse)
async def record_reading_progress(
    book_id: str,
    body: BookProgressRequest,
    user: RequireUser,
    session: SessionDep,
) -> BookProgressResponse:
    """Record reading progress (last-write-wins) for a book."""
    q = select(MediaItem).where(MediaItem.id == book_id, MediaItem.type == "book")
    q = _restrict_to_accessible_libraries(q, user)
    item = await session.scalar(q)
    if item is None:
        raise NotFoundError("Book not found", code="book_not_found")

    prog = await session.get(BookProgress, (user.id, book_id))
    is_completed = body.completed if body.completed is not None else (body.percentage >= 99.0)
    now = utcnow()

    if prog is None:
        prog = BookProgress(
            user_id=user.id,
            book_id=book_id,
            locator=body.locator,
            percentage=body.percentage,
            device_name=body.device_name,
            completed=is_completed,
            updated_at=now,
        )
        session.add(prog)
    else:
        prog.locator = body.locator
        prog.percentage = body.percentage
        prog.device_name = body.device_name
        prog.completed = is_completed
        prog.updated_at = now

    await session.commit()
    return BookProgressResponse(
        book_id=prog.book_id,
        locator=prog.locator,
        percentage=prog.percentage,
        device_name=prog.device_name,
        completed=prog.completed,
        updated_at=prog.updated_at,
    )


# ------------------------------------------------------------- author endpoints


@router.get("/authors", response_model=AuthorListResponse)
async def list_authors(
    user: RequireUser,
    session: SessionDep,
    library_id: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query(description="name, -name, count, -count")] = "name",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuthorListResponse:
    """List authors who have books in accessible libraries."""
    if library_id:
        lib = await session.get(Library, library_id)
        if lib is None or not await user_can_access_library(session, user, library_id):
            raise NotFoundError("Library not found", code="library_not_found")

    # Author with count of books in accessible libraries
    base_q = (
        select(
            Author.id,
            Author.name,
            Author.sort_name,
            func.count(MediaItem.id).label("book_count"),
        )
        .join(Book, Book.author_id == Author.id)
        .join(MediaItem, MediaItem.id == Book.item_id)
        .where(MediaItem.type == "book")
        .group_by(Author.id, Author.name, Author.sort_name)
    )
    base_q = _restrict_to_accessible_libraries(base_q, user)
    if library_id:
        base_q = base_q.where(MediaItem.library_id == library_id)

    # Subquery for total count
    total_q = select(func.count()).select_from(base_q.subquery())
    total = (await session.scalar(total_q)) or 0

    if sort == "-name":
        base_q = base_q.order_by(Author.sort_name.desc().nullslast())
    elif sort == "count":
        base_q = base_q.order_by(func.count(MediaItem.id).asc(), Author.sort_name.asc())
    elif sort == "-count":
        base_q = base_q.order_by(func.count(MediaItem.id).desc(), Author.sort_name.asc())
    else:
        base_q = base_q.order_by(Author.sort_name.asc().nullslast())

    base_q = base_q.offset(offset).limit(limit)
    rows = (await session.execute(base_q)).all()

    items = [
        AuthorListItem(
            id=r.id,
            name=r.name,
            sort_name=r.sort_name,
            book_count=r.book_count,
        )
        for r in rows
    ]
    return AuthorListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/authors/{author_id}", response_model=AuthorDetail)
async def get_author_detail(
    author_id: str,
    user: RequireUser,
    session: SessionDep,
) -> AuthorDetail:
    """Get an author and their books in accessible libraries."""
    author = await session.get(Author, author_id)
    if author is None:
        raise NotFoundError("Author not found", code="author_not_found")

    book_q = (
        select(MediaItem)
        .join(Book, Book.item_id == MediaItem.id)
        .options(
            selectinload(MediaItem.book).selectinload(Book.author),
            selectinload(MediaItem.book).selectinload(Book.series),
            selectinload(MediaItem.files),
            selectinload(MediaItem.images),
        )
        .where(MediaItem.type == "book", Book.author_id == author_id)
        .order_by(MediaItem.sort_title.asc())
    )
    book_q = _restrict_to_accessible_libraries(book_q, user)
    books = list(await session.scalars(book_q))
    if not books and user.role != "admin":
        raise NotFoundError("Author not found", code="author_not_found")

    book_ids = [b.id for b in books]
    prog_map: dict[str, BookProgress] = {}
    if book_ids:
        progs = await session.scalars(
            select(BookProgress).where(
                BookProgress.user_id == user.id,
                BookProgress.book_id.in_(book_ids),
            )
        )
        prog_map = {p.book_id: p for p in progs}

    return AuthorDetail(
        id=author.id,
        name=author.name,
        sort_name=author.sort_name,
        overview=author.overview,
        books=[_to_book_list_item(b, prog_map.get(b.id)) for b in books],
    )


# ------------------------------------------------------------- series endpoints


@router.get("/book-series", response_model=BookSeriesListResponse)
@router.get("/series", response_model=BookSeriesListResponse)
async def list_series(
    user: RequireUser,
    session: SessionDep,
    library_id: Annotated[str | None, Query()] = None,
    sort: Annotated[str, Query(description="name, -name, count, -count")] = "name",
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> BookSeriesListResponse:
    """List book series with book counts in accessible libraries."""
    if library_id:
        lib = await session.get(Library, library_id)
        if lib is None or not await user_can_access_library(session, user, library_id):
            raise NotFoundError("Library not found", code="library_not_found")

    base_q = (
        select(
            BookSeries.id,
            BookSeries.name,
            func.count(MediaItem.id).label("book_count"),
        )
        .join(Book, Book.series_id == BookSeries.id)
        .join(MediaItem, MediaItem.id == Book.item_id)
        .where(MediaItem.type == "book")
        .group_by(BookSeries.id, BookSeries.name)
    )
    base_q = _restrict_to_accessible_libraries(base_q, user)
    if library_id:
        base_q = base_q.where(MediaItem.library_id == library_id)

    total_q = select(func.count()).select_from(base_q.subquery())
    total = (await session.scalar(total_q)) or 0

    if sort == "-name":
        base_q = base_q.order_by(BookSeries.name.desc())
    elif sort == "count":
        base_q = base_q.order_by(func.count(MediaItem.id).asc(), BookSeries.name.asc())
    elif sort == "-count":
        base_q = base_q.order_by(func.count(MediaItem.id).desc(), BookSeries.name.asc())
    else:
        base_q = base_q.order_by(BookSeries.name.asc())

    base_q = base_q.offset(offset).limit(limit)
    rows = (await session.execute(base_q)).all()

    items = [
        BookSeriesListItem(
            id=r.id,
            name=r.name,
            book_count=r.book_count,
        )
        for r in rows
    ]
    return BookSeriesListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/book-series/{series_id}", response_model=BookSeriesDetail)
@router.get("/series/{series_id}", response_model=BookSeriesDetail, include_in_schema=False)
async def get_series_detail(
    series_id: str,
    user: RequireUser,
    session: SessionDep,
) -> BookSeriesDetail:
    """Get a series with its books ordered by series_index."""
    series = await session.get(BookSeries, series_id)
    if series is None:
        raise NotFoundError("Series not found", code="series_not_found")

    book_q = (
        select(MediaItem)
        .join(Book, Book.item_id == MediaItem.id)
        .options(
            selectinload(MediaItem.book).selectinload(Book.author),
            selectinload(MediaItem.book).selectinload(Book.series),
            selectinload(MediaItem.files),
            selectinload(MediaItem.images),
        )
        .where(MediaItem.type == "book", Book.series_id == series_id)
        .order_by(Book.series_index.asc().nullslast(), MediaItem.sort_title.asc())
    )
    book_q = _restrict_to_accessible_libraries(book_q, user)
    books = list(await session.scalars(book_q))
    if not books and user.role != "admin":
        raise NotFoundError("Series not found", code="series_not_found")

    book_ids = [b.id for b in books]
    prog_map: dict[str, BookProgress] = {}
    if book_ids:
        progs = await session.scalars(
            select(BookProgress).where(
                BookProgress.user_id == user.id,
                BookProgress.book_id.in_(book_ids),
            )
        )
        prog_map = {p.book_id: p for p in progs}

    return BookSeriesDetail(
        id=series.id,
        name=series.name,
        description=series.description,
        books=[_to_book_list_item(b, prog_map.get(b.id)) for b in books],
    )
