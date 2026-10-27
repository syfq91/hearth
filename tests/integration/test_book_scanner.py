"""Integration tests for scanning book and comic libraries."""

import io
import zipfile
from pathlib import Path

import pypdf
import pytest
from PIL import Image as PILImage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Author,
    Image,
    Library,
    LibraryPath,
    MediaItem,
)
from app.services.images.pipeline import ImagePipeline
from app.services.metadata.matching import MetadataMatcher
from app.services.scanner.scanner import LibraryScanner


def _create_img(color: str = "red") -> bytes:
    img = PILImage.new("RGB", (30, 40), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _create_epub(
    path: Path,
    title: str = "Dune",
    author: str = "Frank Herbert",
    series: str | None = "Dune Chronicles",
    series_index: float | None = 1.0,
    isbn: str | None = "9780441172719",
) -> None:
    container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
    <rootfiles>
        <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
    </rootfiles>
</container>"""

    series_meta = ""
    if series:
        series_meta += f'<meta name="calibre:series" content="{series}"/>\n'
    if series_index is not None:
        series_meta += f'<meta name="calibre:series_index" content="{series_index}"/>\n'

    isbn_tag = f'<dc:identifier id="isbn" opf:scheme="ISBN">{isbn}</dc:identifier>' if isbn else ""

    opf_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
        <dc:title>{title}</dc:title>
        <dc:creator>{author}</dc:creator>
        {isbn_tag}
        <dc:language>en</dc:language>
        <dc:publisher>Chilton Books</dc:publisher>
        <dc:date>1965-08-01</dc:date>
        {series_meta}
    </metadata>
    <manifest>
        <item id="cover" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>
    </manifest>
</package>"""

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)
        zf.writestr("OEBPS/cover.jpg", _create_img(color="blue"))


def _create_cbz(path: Path, title: str, series: str, number: str, writer: str) -> None:
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
        zf.writestr("01.jpg", _create_img(color="green"))
        zf.writestr("02.jpg", _create_img(color="yellow"))


def _create_pdf(path: Path, title: str, author: str) -> None:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=300)
    writer.add_metadata({"/Title": title, "/Author": author})
    with open(path, "wb") as f:
        writer.write(f)


@pytest.mark.asyncio
async def test_scan_book_library(
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    db_settings.scan_missing_grace_hours = 0
    media_root = db_settings.media_dir / "books_test"
    media_root.mkdir(parents=True)

    # 1. Book with multiple formats: Dune.epub and Dune.pdf in Dune folder
    dune_dir = media_root / "Frank Herbert" / "Dune"
    dune_dir.mkdir(parents=True)
    _create_epub(dune_dir / "Dune.epub")
    _create_pdf(dune_dir / "Dune.pdf", title="Dune", author="Frank Herbert")

    # 2. Comic: Watchmen #1
    comics_dir = media_root / "Alan Moore" / "Watchmen"
    comics_dir.mkdir(parents=True)
    _create_cbz(
        comics_dir / "Watchmen #1.cbz",
        title="Chapter 1",
        series="Watchmen",
        number="1",
        writer="Alan Moore",
    )

    # 3. Create Library in database
    lib_id = "lib-books-1"
    async with db_sessionmaker() as session:
        lib = Library(id=lib_id, name="Books", type="book")
        lib.paths.append(LibraryPath(id="lp-b1", library_id=lib_id, path=str(media_root)))
        session.add(lib)
        await session.commit()

    # 4. Run Scanner with ImagePipeline
    img_pipeline = ImagePipeline(settings=db_settings, sessionmaker=db_sessionmaker)
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[], artwork=img_pipeline)
    scanner = LibraryScanner(db_settings, db_sessionmaker, matcher=matcher)
    stats = await scanner.scan_library(lib_id)

    assert stats.added == 3  # 3 files total: 2 for Dune, 1 for Watchmen
    assert stats.added_items == 2  # 2 distinct book items: Dune and Watchmen
    assert stats.images_stored >= 1

    # 5. Verify database contents
    async with db_sessionmaker() as session:
        items = list(await session.scalars(select(MediaItem).where(MediaItem.library_id == lib_id)))
        assert len(items) == 2

        dune_item = next(it for it in items if it.title == "Dune")
        assert dune_item.type == "book"
        assert len(dune_item.files) == 2
        file_containers = {f.container for f in dune_item.files}
        assert file_containers == {"epub", "pdf"}

        assert dune_item.book is not None
        assert dune_item.book.isbn == "9780441172719"
        assert dune_item.book.author is not None
        assert dune_item.book.author.name == "Frank Herbert"
        assert dune_item.book.series is not None
        assert dune_item.book.series.name == "Dune Chronicles"
        assert dune_item.book.series_index == 1.0

        # Check artwork stored
        dune_images = list(
            await session.scalars(select(Image).where(Image.item_id == dune_item.id))
        )
        assert len(dune_images) >= 1
        assert dune_images[0].kind == "poster"

        watchmen_item = next(it for it in items if "Watchmen" in it.title or it.book.series)
        assert watchmen_item.book.author is not None
        assert watchmen_item.book.author.name == "Alan Moore"
        assert watchmen_item.book.series.name == "Watchmen"

    # 6. Delete one format of Dune (e.g. Dune.pdf) and re-scan: Book item should stay!
    (dune_dir / "Dune.pdf").unlink()
    stats2 = await scanner.scan_library(lib_id)
    assert stats2.removed == 1

    async with db_sessionmaker() as session:
        reloaded_dune = await session.get(MediaItem, dune_item.id)
        assert reloaded_dune is not None
        assert len(reloaded_dune.files) == 1
        assert reloaded_dune.files[0].container == "epub"

    # 7. Delete all files in Watchmen: Book, Author and Series should be cleaned up!
    (comics_dir / "Watchmen #1.cbz").unlink()
    stats3 = await scanner.scan_library(lib_id)
    assert stats3.removed == 1

    async with db_sessionmaker() as session:
        remaining_items = list(
            await session.scalars(select(MediaItem).where(MediaItem.library_id == lib_id))
        )
        assert len(remaining_items) == 1
        assert remaining_items[0].id == dune_item.id

        authors = list(await session.scalars(select(Author)))
        author_names = {a.name for a in authors}
        assert "Frank Herbert" in author_names
        assert "Alan Moore" not in author_names
