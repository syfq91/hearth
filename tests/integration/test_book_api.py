"""Integration tests for native Books and Reading Progress API."""

import io
import uuid

import httpx
import pytest
from PIL import Image as PILImage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import Author, Book, BookSeries, Library, LibraryPath, MediaFile, MediaItem

ADMIN = {"username": "admin", "password": "correct horse"}


def _create_img(color: str = "blue") -> bytes:
    img = PILImage.new("RGB", (30, 40), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


async def _setup(api_client: httpx.AsyncClient) -> dict[str, str]:
    response = await api_client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _add_user(
    api_client: httpx.AsyncClient, admin: dict[str, str], username: str, library_ids: list[str]
) -> dict[str, str]:
    created = await api_client.post(
        "/api/v1/users",
        json={
            "username": username,
            "password": "correct horse",
            "role": "user",
            "library_ids": library_ids,
        },
        headers=_bearer(admin),
    )
    assert created.status_code == 201, created.text
    response = await api_client.post(
        "/api/v1/auth/login", json={"username": username, "password": "correct horse"}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_books_api_endpoints_and_search(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin_tokens = await _setup(api_client)
    headers = _bearer(admin_tokens)

    # 1. Create a Book library with media folder
    lib_id = f"lib-{uuid.uuid4().hex[:8]}"
    media_dir = db_settings.media_dir / f"books_{uuid.uuid4().hex[:8]}"
    media_dir.mkdir(parents=True)

    # Create dummy epub file on disk
    epub_path = media_dir / "Dune.epub"
    with open(epub_path, "wb") as f:
        f.write(b"dummy epub binary content for range testing")

    # 2. Seed database rows: Author, Series, Book, MediaItem, MediaFile
    author_id = f"auth-{uuid.uuid4().hex[:8]}"
    series_id = f"ser-{uuid.uuid4().hex[:8]}"
    book_id = f"book-{uuid.uuid4().hex[:8]}"
    file_id = f"file-{uuid.uuid4().hex[:8]}"
    now = utcnow()

    async with db_sessionmaker() as session:
        lib = Library(id=lib_id, name="Sci-Fi Books", type="book")
        lp_id = f"lp-{uuid.uuid4().hex[:8]}"
        lib.paths.append(LibraryPath(id=lp_id, library_id=lib_id, path=str(media_dir)))
        session.add(lib)

        author = Author(id=author_id, name="Frank Herbert", sort_name="Herbert, Frank")
        series = BookSeries(id=series_id, name="Dune Chronicles")
        session.add_all([author, series])

        item = MediaItem(
            id=book_id,
            library_id=lib_id,
            type="book",
            title="Dune",
            sort_title="Dune",
            year=1965,
            overview="Set on the desert planet Arrakis.",
            added_at=now,
        )
        book = Book(
            item_id=book_id,
            author_id=author_id,
            series_id=series_id,
            series_index=1.0,
            isbn="9780441172719",
            publisher="Chilton Books",
            published_date="1965-08-01",
            page_count=412,
            language="en",
        )
        mfile = MediaFile(
            id=file_id,
            item_id=book_id,
            path=str(epub_path),
            size_bytes=len(b"dummy epub binary content for range testing"),
            container="epub",
            partial_hash="dummyhash123",
            mtime=now.timestamp(),
        )
        session.add_all([item, book, mfile])
        await session.commit()

    # 3. List books
    resp = await api_client.get("/api/v1/books", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    book_entry = data["items"][0]
    assert book_entry["id"] == book_id
    assert book_entry["title"] == "Dune"
    assert book_entry["author"]["name"] == "Frank Herbert"
    assert book_entry["series"]["name"] == "Dune Chronicles"
    assert book_entry["series"]["index"] == 1.0
    assert book_entry["formats"] == ["epub"]

    # Filter by library_id
    resp = await api_client.get(f"/api/v1/books?library_id={lib_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    resp = await api_client.get("/api/v1/books?library_id=nonexistent", headers=headers)
    assert resp.status_code == 404

    # 4. Detail book
    resp = await api_client.get(f"/api/v1/books/{book_id}", headers=headers)
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["id"] == book_id
    assert detail["isbn"] == "9780441172719"
    assert detail["publisher"] == "Chilton Books"
    assert len(detail["files"]) == 1
    assert detail["files"][0]["container"] == "epub"

    # 5. Search books
    # By title
    resp = await api_client.get("/api/v1/books/search?q=Dune", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["title"] == "Dune"

    # By author
    resp = await api_client.get("/api/v1/books/search?q=Herbert", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # By ISBN
    resp = await api_client.get("/api/v1/books/search?q=9780441172719", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # Empty search query validation
    resp = await api_client.get("/api/v1/books/search?q=", headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_authors_and_series_endpoints(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin_tokens = await _setup(api_client)
    headers = _bearer(admin_tokens)

    lib_id = f"lib-{uuid.uuid4().hex[:8]}"
    author_id = f"auth-{uuid.uuid4().hex[:8]}"
    series_id = f"ser-{uuid.uuid4().hex[:8]}"
    book_id = f"book-{uuid.uuid4().hex[:8]}"
    now = utcnow()

    async with db_sessionmaker() as session:
        lib = Library(id=lib_id, name="Comics", type="book")
        session.add(lib)
        author = Author(id=author_id, name="Alan Moore", sort_name="Moore, Alan")
        series = BookSeries(id=series_id, name="Watchmen")
        session.add_all([author, series])

        item = MediaItem(
            id=book_id,
            library_id=lib_id,
            type="book",
            title="Watchmen #1",
            sort_title="Watchmen #1",
            added_at=now,
        )
        book = Book(
            item_id=book_id,
            author_id=author_id,
            series_id=series_id,
            series_index=1.0,
        )
        session.add_all([item, book])
        await session.commit()

    # 1. List authors
    resp = await api_client.get("/api/v1/authors", headers=headers)
    assert resp.status_code == 200
    authors = resp.json()["items"]
    assert any(a["name"] == "Alan Moore" and a["book_count"] == 1 for a in authors)

    # 2. Author detail
    resp = await api_client.get(f"/api/v1/authors/{author_id}", headers=headers)
    assert resp.status_code == 200
    auth_detail = resp.json()
    assert auth_detail["name"] == "Alan Moore"
    assert len(auth_detail["books"]) == 1
    assert auth_detail["books"][0]["title"] == "Watchmen #1"

    # 3. List series
    resp = await api_client.get("/api/v1/series", headers=headers)
    assert resp.status_code == 200
    series_list = resp.json()["items"]
    assert any(s["name"] == "Watchmen" and s["book_count"] == 1 for s in series_list)

    # 4. Series detail
    resp = await api_client.get(f"/api/v1/series/{series_id}", headers=headers)
    assert resp.status_code == 200
    ser_detail = resp.json()
    assert ser_detail["name"] == "Watchmen"
    assert len(ser_detail["books"]) == 1


@pytest.mark.asyncio
async def test_book_file_download_and_range_requests(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin_tokens = await _setup(api_client)
    headers = _bearer(admin_tokens)

    lib_id = f"lib-{uuid.uuid4().hex[:8]}"
    media_dir = db_settings.media_dir / f"books_{uuid.uuid4().hex[:8]}"
    media_dir.mkdir(parents=True)
    epub_path = media_dir / "Neuromancer.epub"
    content = b"The sky above the port was the color of television, tuned to a dead channel."
    with open(epub_path, "wb") as f:
        f.write(content)

    book_id = f"book-{uuid.uuid4().hex[:8]}"
    file_id = f"file-{uuid.uuid4().hex[:8]}"
    now = utcnow()

    async with db_sessionmaker() as session:
        lib = Library(id=lib_id, name="Cyberpunk", type="book")
        lp_id = f"lp-{uuid.uuid4().hex[:8]}"
        lib.paths.append(LibraryPath(id=lp_id, library_id=lib_id, path=str(media_dir)))
        session.add(lib)

        item = MediaItem(
            id=book_id,
            library_id=lib_id,
            type="book",
            title="Neuromancer",
            sort_title="Neuromancer",
            added_at=now,
        )
        book = Book(item_id=book_id)
        mfile = MediaFile(
            id=file_id,
            item_id=book_id,
            path=str(epub_path),
            size_bytes=len(content),
            container="epub",
            partial_hash="dummyhash123",
            mtime=now.timestamp(),
        )
        session.add_all([item, book, mfile])
        await session.commit()

    # 1. Full download with Content-Disposition
    resp = await api_client.get(f"/api/v1/books/{book_id}/file/epub", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["Content-Disposition"] == 'attachment; filename="Neuromancer.epub"'
    assert resp.headers["Accept-Ranges"] == "bytes"
    assert resp.content == content

    # 2. HTTP Range request
    range_headers = {**headers, "Range": "bytes=0-14"}
    range_resp = await api_client.get(f"/api/v1/books/{book_id}/file/epub", headers=range_headers)
    assert range_resp.status_code == 206
    assert range_resp.headers["Content-Range"] == f"bytes 0-14/{len(content)}"
    assert range_resp.content == content[:15]

    # 3. Format not available
    resp404 = await api_client.get(f"/api/v1/books/{book_id}/file/pdf", headers=headers)
    assert resp404.status_code == 404


@pytest.mark.asyncio
async def test_reading_progress_cross_client_sync(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Acceptance criterion 3: Progress written from one client is returned to another."""
    admin_tokens = await _setup(api_client)
    user_tokens = await _add_user(api_client, admin_tokens, "reader1", [])

    # Create library and grant access to reader1
    lib_id = f"lib-{uuid.uuid4().hex[:8]}"
    book_id = f"book-{uuid.uuid4().hex[:8]}"
    now = utcnow()

    async with db_sessionmaker() as session:
        lib = Library(id=lib_id, name="Sci-Fi", type="book")
        session.add(lib)
        item = MediaItem(
            id=book_id,
            library_id=lib_id,
            type="book",
            title="Snow Crash",
            sort_title="Snow Crash",
            added_at=now,
        )
        book = Book(item_id=book_id)
        session.add_all([item, book])
        await session.commit()

    # Grant library access to reader1 via admin
    user_info = await api_client.get("/api/v1/auth/me", headers=_bearer(user_tokens))
    user_id = user_info.json()["id"]
    await api_client.patch(
        f"/api/v1/users/{user_id}",
        json={"library_ids": [lib_id]},
        headers=_bearer(admin_tokens),
    )

    client1_headers = _bearer(user_tokens)
    client2_headers = _bearer(user_tokens)

    # 1. Initially no progress
    resp = await api_client.get(f"/api/v1/books/{book_id}/progress", headers=client1_headers)
    assert resp.status_code == 404

    # 2. Client 1 (e.g. Web Reader on desktop) updates reading progress
    put_payload = {
        "locator": "epubcfi(/6/4[chap01]!/4/2/10)",
        "percentage": 18.5,
        "device_name": "Web Chrome Desktop",
        "completed": False,
    }
    put_resp = await api_client.put(
        f"/api/v1/books/{book_id}/progress",
        json=put_payload,
        headers=client1_headers,
    )
    assert put_resp.status_code == 200
    saved = put_resp.json()
    assert saved["locator"] == "epubcfi(/6/4[chap01]!/4/2/10)"
    assert saved["percentage"] == 18.5
    assert saved["device_name"] == "Web Chrome Desktop"
    assert saved["completed"] is False

    # 3. Client 2 (e.g. Mobile E-Reader app) reads progress
    get_resp = await api_client.get(
        f"/api/v1/books/{book_id}/progress",
        headers=client2_headers,
    )
    assert get_resp.status_code == 200
    sync_data = get_resp.json()
    assert sync_data["book_id"] == book_id
    assert sync_data["locator"] == "epubcfi(/6/4[chap01]!/4/2/10)"
    assert sync_data["percentage"] == 18.5
    assert sync_data["device_name"] == "Web Chrome Desktop"
    assert sync_data["completed"] is False

    # 4. Client 2 advances and marks completed
    put_payload_2 = {
        "locator": "epubcfi(/6/12[epilogue]!/4/2/40)",
        "percentage": 100.0,
        "device_name": "Kobo Clara E-Reader",
        "completed": True,
    }
    put_resp_2 = await api_client.put(
        f"/api/v1/books/{book_id}/progress",
        json=put_payload_2,
        headers=client2_headers,
    )
    assert put_resp_2.status_code == 200
    assert put_resp_2.json()["percentage"] == 100.0
    assert put_resp_2.json()["completed"] is True

    # 5. Client 1 requests book detail and sees the updated progress
    detail_resp = await api_client.get(
        f"/api/v1/books/{book_id}",
        headers=client1_headers,
    )
    assert detail_resp.status_code == 200
    book_detail = detail_resp.json()
    assert book_detail["progress"] is not None
    assert book_detail["progress"]["locator"] == "epubcfi(/6/12[epilogue]!/4/2/40)"
    assert book_detail["progress"]["percentage"] == 100.0
    assert book_detail["progress"]["device_name"] == "Kobo Clara E-Reader"
    assert book_detail["progress"]["completed"] is True


@pytest.mark.asyncio
async def test_book_library_access_control(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Non-admin user without access to the library cannot see or access books."""
    admin_tokens = await _setup(api_client)
    restricted_user = await _add_user(api_client, admin_tokens, "restricted", [])

    lib_id = f"lib-{uuid.uuid4().hex[:8]}"
    book_id = f"book-{uuid.uuid4().hex[:8]}"
    now = utcnow()

    async with db_sessionmaker() as session:
        lib = Library(id=lib_id, name="Secret Books", type="book")
        session.add(lib)
        item = MediaItem(
            id=book_id,
            library_id=lib_id,
            type="book",
            title="Top Secret Novel",
            sort_title="Top Secret Novel",
            added_at=now,
        )
        book = Book(item_id=book_id)
        session.add_all([item, book])
        await session.commit()

    restricted_headers = _bearer(restricted_user)

    # 1. Listing returns 0 books
    resp = await api_client.get("/api/v1/books", headers=restricted_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0

    # 2. Filter by inaccessible library returns 404
    resp = await api_client.get(f"/api/v1/books?library_id={lib_id}", headers=restricted_headers)
    assert resp.status_code == 404

    # 3. Book detail returns 404
    resp = await api_client.get(f"/api/v1/books/{book_id}", headers=restricted_headers)
    assert resp.status_code == 404

    # 4. Search returns 0 items
    resp = await api_client.get("/api/v1/books/search?q=Secret", headers=restricted_headers)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_comic_page_extraction_and_caching(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin_tokens = await _setup(api_client)
    headers = _bearer(admin_tokens)

    lib_id = f"lib-{uuid.uuid4().hex[:8]}"
    media_dir = db_settings.media_dir / f"books_{uuid.uuid4().hex[:8]}"
    media_dir.mkdir(parents=True)

    # Create CBZ file with 3 colored pages
    cbz_path = media_dir / "Saga #1.cbz"
    import zipfile

    with zipfile.ZipFile(cbz_path, "w") as zf:
        zf.writestr("01.jpg", _create_img(color="red"))
        zf.writestr("02.jpg", _create_img(color="green"))
        zf.writestr("03.jpg", _create_img(color="blue"))

    book_id = f"book-{uuid.uuid4().hex[:8]}"
    file_id = f"file-{uuid.uuid4().hex[:8]}"
    now = utcnow()

    async with db_sessionmaker() as session:
        lib = Library(id=lib_id, name="Comics", type="book")
        lp_id = f"lp-{uuid.uuid4().hex[:8]}"
        lib.paths.append(LibraryPath(id=lp_id, library_id=lib_id, path=str(media_dir)))
        session.add(lib)

        item = MediaItem(
            id=book_id,
            library_id=lib_id,
            type="book",
            title="Saga #1",
            sort_title="Saga #1",
            added_at=now,
        )
        book = Book(item_id=book_id)
        mfile = MediaFile(
            id=file_id,
            item_id=book_id,
            path=str(cbz_path),
            size_bytes=cbz_path.stat().st_size,
            container="cbz",
            partial_hash="dummycbz123",
            mtime=now.timestamp(),
        )
        session.add_all([item, book, mfile])
        await session.commit()

    # 1. Request page 1: Cache MISS
    resp1 = await api_client.get(f"/api/v1/books/{book_id}/pages/1", headers=headers)
    assert resp1.status_code == 200
    assert resp1.headers.get("X-Cache") == "MISS"
    assert resp1.headers.get("Cache-Control") == "private, max-age=86400"
    assert len(resp1.content) > 0

    # 2. Request page 1 again: Cache HIT
    resp2 = await api_client.get(f"/api/v1/books/{book_id}/pages/1", headers=headers)
    assert resp2.status_code == 200
    assert resp2.headers.get("X-Cache") == "HIT"
    assert resp2.content == resp1.content

    # 3. Request page 1 resized with width=20: returns resized image
    resp_resized = await api_client.get(
        f"/api/v1/books/{book_id}/pages/1?width=50", headers=headers
    )
    assert resp_resized.status_code == 200
    assert resp_resized.headers.get("X-Cache") == "MISS"
    with PILImage.open(io.BytesIO(resp_resized.content)) as im:
        assert im.width <= 50

    # 4. Request page out of bounds: returns 404
    resp_oob = await api_client.get(f"/api/v1/books/{book_id}/pages/99", headers=headers)
    assert resp_oob.status_code == 404
    assert resp_oob.json()["error"]["code"] == "page_not_found"

    # 5. Access via media token query parameter
    token_resp = await api_client.post(
        "/api/v1/auth/media-tokens",
        json={"item_id": book_id},
        headers=headers,
    )
    assert token_resp.status_code == 201
    media_token = token_resp.json()["token"]

    resp_token = await api_client.get(f"/api/v1/books/{book_id}/pages/2?token={media_token}")
    assert resp_token.status_code == 200
    assert len(resp_token.content) > 0
