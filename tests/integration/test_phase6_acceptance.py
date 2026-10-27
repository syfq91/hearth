"""Phase 6 acceptance tests verifying all three acceptance criteria.

Criteria:
1. Fixture EPUB, PDF and CBZ files produce correct title, author, series and cover.
2. Native book API endpoints (browsing, search, download, comic page extraction, reading progress)
   pass automated tests against fixture files.
3. Progress written from one client is returned to another.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path

import httpx
import pypdf
import pytest
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Image, Library, LibraryPath, MediaItem
from app.services.images.pipeline import ImagePipeline
from app.services.metadata.matching import MetadataMatcher
from app.services.scanner.scanner import LibraryScanner

ADMIN = {"username": "admin", "password": "correct horse"}


def _create_img(width: int = 60, height: int = 80, color: str = "blue") -> bytes:
    img = PILImage.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _create_fixture_epub(
    path: Path,
    title: str = "Dune",
    author: str = "Frank Herbert",
    series: str = "Dune Chronicles",
    series_index: float = 1.0,
    isbn: str = "9780441172719",
) -> None:
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>"""

    opf_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
        <dc:title>{title}</dc:title>
        <dc:creator>{author}</dc:creator>
        <dc:identifier id="isbn" opf:scheme="ISBN">{isbn}</dc:identifier>
        <dc:language>en</dc:language>
        <dc:publisher>Chilton Books</dc:publisher>
        <dc:date>1965-08-01</dc:date>
        <meta name="calibre:series" content="{series}"/>
        <meta name="calibre:series_index" content="{series_index}"/>
    </metadata>
    <manifest>
        <item id="cover" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>
    </manifest>
</package>"""

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)
        zf.writestr("OEBPS/cover.jpg", _create_img(color="purple"))


def _create_fixture_cbz(
    path: Path,
    title: str = "Chapter 1",
    series: str = "Watchmen",
    number: str = "1",
    writer: str = "Alan Moore",
) -> None:
    comic_info = f"""<?xml version="1.0"?>
<ComicInfo>
  <Title>{title}</Title>
  <Series>{series}</Series>
  <Number>{number}</Number>
  <Writer>{writer}</Writer>
  <PageCount>2</PageCount>
</ComicInfo>"""

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", comic_info)
        zf.writestr("01.jpg", _create_img(color="yellow"))
        zf.writestr("02.jpg", _create_img(color="black"))


def _create_fixture_pdf(
    path: Path,
    title: str = "Neuromancer",
    author: str = "William Gibson",
) -> None:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.add_metadata({"/Title": title, "/Author": author})
    with open(path, "wb") as f:
        writer.write(f)


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
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


async def _scan_fixture_library(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    media_dir: Path,
) -> tuple[str, str, str, str]:
    """Helper to create files and scan library into database."""
    # Create files
    epub_dir = media_dir / "Frank Herbert" / "Dune"
    epub_dir.mkdir(parents=True)
    epub_path = epub_dir / "Dune.epub"
    _create_fixture_epub(epub_path)

    comic_dir = media_dir / "Alan Moore" / "Watchmen"
    comic_dir.mkdir(parents=True)
    cbz_path = comic_dir / "Watchmen #1.cbz"
    _create_fixture_cbz(cbz_path)

    pdf_dir = media_dir / "William Gibson" / "Sprawl"
    pdf_dir.mkdir(parents=True)
    pdf_path = pdf_dir / "Neuromancer.pdf"
    _create_fixture_pdf(pdf_path)

    lib_id = f"lib-books-{uuid.uuid4().hex[:8]}"
    async with sessionmaker() as session:
        lib = Library(id=lib_id, name="Phase 6 Library", type="book")
        lp_id = f"lp-{uuid.uuid4().hex[:8]}"
        lib.paths.append(LibraryPath(id=lp_id, library_id=lib_id, path=str(media_dir)))
        session.add(lib)
        await session.commit()

    img_pipeline = ImagePipeline(settings=settings, sessionmaker=sessionmaker)
    matcher = MetadataMatcher(settings, sessionmaker, providers=[], artwork=img_pipeline)
    scanner = LibraryScanner(settings, sessionmaker, matcher=matcher)
    stats = await scanner.scan_library(lib_id)

    assert stats.added == 3
    assert stats.added_items == 3

    async with sessionmaker() as session:
        items = list(await session.scalars(select(MediaItem).where(MediaItem.library_id == lib_id)))
        dune_id = next(it.id for it in items if it.title == "Dune")
        watchmen_id = next(
            it.id
            for it in items
            if "Watchmen" in it.title
            or (it.book and it.book.series and "Watchmen" in it.book.series.name)
        )
        neuro_id = next(it.id for it in items if it.title == "Neuromancer")

    return lib_id, dune_id, watchmen_id, neuro_id


@pytest.mark.asyncio
async def test_acceptance_criterion_1_fixture_extraction_and_covers(
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Criterion 1: Fixtures produce correct title, author, series and cover."""
    media_dir = db_settings.media_dir / f"p6_c1_{uuid.uuid4().hex[:8]}"
    media_dir.mkdir(parents=True)

    lib_id, dune_id, watchmen_id, neuro_id = await _scan_fixture_library(
        db_settings, db_sessionmaker, media_dir
    )

    async with db_sessionmaker() as session:
        # 1. EPUB: Dune
        dune = await session.get(MediaItem, dune_id)
        assert dune is not None
        assert dune.title == "Dune"
        assert dune.book is not None
        assert dune.book.author is not None
        assert dune.book.author.name == "Frank Herbert"
        assert dune.book.series is not None
        assert dune.book.series.name == "Dune Chronicles"
        assert dune.book.series_index == 1.0
        assert dune.book.isbn == "9780441172719"

        # Check cover stored for Dune
        dune_images = list(await session.scalars(select(Image).where(Image.item_id == dune_id)))
        assert len(dune_images) >= 1
        assert dune_images[0].kind == "poster"
        assert Path(dune_images[0].local_path).is_file()

        # 2. CBZ: Watchmen
        watchmen = await session.get(MediaItem, watchmen_id)
        assert watchmen is not None
        assert watchmen.book is not None
        assert watchmen.book.author is not None
        assert watchmen.book.author.name == "Alan Moore"
        assert watchmen.book.series is not None
        assert watchmen.book.series.name == "Watchmen"
        assert watchmen.book.series_index == 1.0

        watchmen_images = list(
            await session.scalars(select(Image).where(Image.item_id == watchmen_id))
        )
        assert len(watchmen_images) >= 1
        assert watchmen_images[0].kind == "poster"
        assert Path(watchmen_images[0].local_path).is_file()

        # 3. PDF: Neuromancer
        neuro = await session.get(MediaItem, neuro_id)
        assert neuro is not None
        assert neuro.title == "Neuromancer"
        assert neuro.book is not None
        assert neuro.book.author is not None
        assert neuro.book.author.name == "William Gibson"

        neuro_images = list(await session.scalars(select(Image).where(Image.item_id == neuro_id)))
        assert len(neuro_images) >= 1
        assert neuro_images[0].kind == "poster"
        assert Path(neuro_images[0].local_path).is_file()


@pytest.mark.asyncio
async def test_acceptance_criterion_2_native_book_api_endpoints(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Criterion 2: Book API endpoints pass automated tests against fixture files."""
    admin_tokens = await _setup(api_client)
    headers = _bearer(admin_tokens)

    media_dir = db_settings.media_dir / f"p6_c2_{uuid.uuid4().hex[:8]}"
    media_dir.mkdir(parents=True)

    lib_id, dune_id, watchmen_id, neuro_id = await _scan_fixture_library(
        db_settings, db_sessionmaker, media_dir
    )

    # 1. Browsing: GET /api/v1/books
    resp = await api_client.get("/api/v1/books", headers=headers)
    assert resp.status_code == 200
    catalog = resp.json()
    assert catalog["total"] == 3
    assert len(catalog["items"]) == 3

    # Filter by library_id
    resp_lib = await api_client.get(f"/api/v1/books?library_id={lib_id}", headers=headers)
    assert resp_lib.status_code == 200
    assert resp_lib.json()["total"] == 3

    # Sorting
    resp_sort = await api_client.get("/api/v1/books?sort=author", headers=headers)
    assert resp_sort.status_code == 200
    sorted_titles = [b["title"] for b in resp_sort.json()["items"]]
    assert len(sorted_titles) == 3

    # 2. Book Detail: GET /api/v1/books/{id}
    detail_resp = await api_client.get(f"/api/v1/books/{dune_id}", headers=headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["title"] == "Dune"
    assert detail["author"]["name"] == "Frank Herbert"
    assert detail["series"]["name"] == "Dune Chronicles"
    assert detail["cover_image_id"] is not None
    assert detail["formats"] == ["epub"]

    # 3. Authors: GET /api/v1/authors and GET /api/v1/authors/{id}
    authors_resp = await api_client.get("/api/v1/authors", headers=headers)
    assert authors_resp.status_code == 200
    author_items = authors_resp.json()["items"]
    assert len(author_items) == 3
    frank = next(a for a in author_items if a["name"] == "Frank Herbert")
    assert frank["book_count"] == 1

    frank_resp = await api_client.get(f"/api/v1/authors/{frank['id']}", headers=headers)
    assert frank_resp.status_code == 200
    assert frank_resp.json()["name"] == "Frank Herbert"
    assert len(frank_resp.json()["books"]) == 1

    # 4. Series: GET /api/v1/series and GET /api/v1/series/{id}
    series_resp = await api_client.get("/api/v1/series", headers=headers)
    assert series_resp.status_code == 200
    series_items = series_resp.json()["items"]
    assert len(series_items) >= 2
    watch_series = next(s for s in series_items if s["name"] == "Watchmen")

    watch_series_resp = await api_client.get(
        f"/api/v1/series/{watch_series['id']}", headers=headers
    )
    assert watch_series_resp.status_code == 200
    assert watch_series_resp.json()["name"] == "Watchmen"
    assert len(watch_series_resp.json()["books"]) == 1

    # 5. Search: GET /api/v1/books/search
    search_resp = await api_client.get("/api/v1/books/search?q=Herbert", headers=headers)
    assert search_resp.status_code == 200
    assert search_resp.json()["total"] == 1
    assert search_resp.json()["items"][0]["title"] == "Dune"

    # 6. Format Download: GET /api/v1/books/{id}/file/{format} with HTTP Range
    download_resp = await api_client.get(
        f"/api/v1/books/{dune_id}/file/epub",
        headers={**headers, "Range": "bytes=0-49"},
    )
    assert download_resp.status_code == 206
    assert download_resp.headers["Accept-Ranges"] == "bytes"
    assert download_resp.headers["Content-Range"].startswith("bytes 0-49/")
    assert len(download_resp.content) == 50

    # 7. Comic Page Serving: GET /api/v1/books/{id}/pages/{n} with resizing
    page_resp = await api_client.get(
        f"/api/v1/books/{watchmen_id}/pages/1?width=120", headers=headers
    )
    assert page_resp.status_code == 200
    assert page_resp.headers.get("X-Cache") == "MISS"
    with PILImage.open(io.BytesIO(page_resp.content)) as im:
        assert im.width <= 120

    # Second request hits cache
    page_resp_cached = await api_client.get(
        f"/api/v1/books/{watchmen_id}/pages/1?width=120", headers=headers
    )
    assert page_resp_cached.status_code == 200
    assert page_resp_cached.headers.get("X-Cache") == "HIT"


@pytest.mark.asyncio
async def test_acceptance_criterion_3_cross_client_reading_progress_sync(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Acceptance Criterion 3: Progress written from one client is returned to another."""
    admin_tokens = await _setup(api_client)
    user_tokens = await _add_user(api_client, admin_tokens, "syncreader", [])

    media_dir = db_settings.media_dir / f"p6_c3_{uuid.uuid4().hex[:8]}"
    media_dir.mkdir(parents=True)

    lib_id, dune_id, _, _ = await _scan_fixture_library(db_settings, db_sessionmaker, media_dir)

    # Grant library to syncreader
    user_info = (await api_client.get("/api/v1/auth/me", headers=_bearer(user_tokens))).json()
    await api_client.patch(
        f"/api/v1/users/{user_info['id']}",
        json={"library_ids": [lib_id]},
        headers=_bearer(admin_tokens),
    )

    client_a_headers = _bearer(user_tokens)
    client_b_headers = _bearer(user_tokens)

    # 1. Client A updates reading progress
    progress_a = {
        "locator": "epubcfi(/6/14[chap03]!/4/2/18)",
        "percentage": 37.5,
        "device_name": "Kobo Clara 2E",
        "completed": False,
    }
    put_a = await api_client.put(
        f"/api/v1/books/{dune_id}/progress",
        json=progress_a,
        headers=client_a_headers,
    )
    assert put_a.status_code == 200
    assert put_a.json()["locator"] == "epubcfi(/6/14[chap03]!/4/2/18)"
    assert put_a.json()["percentage"] == 37.5
    assert put_a.json()["device_name"] == "Kobo Clara 2E"

    # 2. Client B gets reading progress and receives the exact updated position
    get_b = await api_client.get(
        f"/api/v1/books/{dune_id}/progress",
        headers=client_b_headers,
    )
    assert get_b.status_code == 200
    data_b = get_b.json()
    assert data_b["book_id"] == dune_id
    assert data_b["locator"] == "epubcfi(/6/14[chap03]!/4/2/18)"
    assert data_b["percentage"] == 37.5
    assert data_b["device_name"] == "Kobo Clara 2E"
    assert data_b["completed"] is False

    # 3. Client B finishes reading on iPad
    progress_b = {
        "locator": "epubcfi(/6/40[afterword]!/4/2/50)",
        "percentage": 100.0,
        "device_name": "iPad Pro Safari",
        "completed": True,
    }
    put_b = await api_client.put(
        f"/api/v1/books/{dune_id}/progress",
        json=progress_b,
        headers=client_b_headers,
    )
    assert put_b.status_code == 200

    # 4. Client A inspects book detail and verifies completed status and updated percentage
    detail_a = await api_client.get(
        f"/api/v1/books/{dune_id}",
        headers=client_a_headers,
    )
    assert detail_a.status_code == 200
    d_info = detail_a.json()
    assert d_info["progress"] is not None
    assert d_info["progress"]["locator"] == "epubcfi(/6/40[afterword]!/4/2/50)"
    assert d_info["progress"]["percentage"] == 100.0
    assert d_info["progress"]["device_name"] == "iPad Pro Safari"
    assert d_info["progress"]["completed"] is True
