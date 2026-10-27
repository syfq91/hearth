"""Unit tests for book readers: EPUB, CBZ, PDF, and CBR."""

import io
import zipfile
from pathlib import Path

import pypdf
import pytest
from PIL import Image as PILImage

from app.services.media.books import (
    extract_comic_page,
    read_book_metadata,
    read_cbz_metadata,
    read_epub_metadata,
    read_pdf_metadata,
)


def _create_test_image(width: int = 20, height: int = 30, color: str = "red") -> bytes:
    img = PILImage.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def _create_minimal_epub(
    path: Path,
    title: str = "Dune",
    author: str = "Frank Herbert",
    series: str | None = "Dune Chronicles",
    series_index: float | None = 1.0,
    isbn: str | None = "9780441172719",
    overview: str | None = "A science fiction masterpiece.",
    include_cover: bool = True,
) -> None:
    cover_bytes = _create_test_image(color="blue") if include_cover else None

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
    overview_tag = f"<dc:description>{overview}</dc:description>" if overview else ""
    cover_item = (
        '<item id="cover-img" href="images/cover.jpg" '
        'media-type="image/jpeg" properties="cover-image"/>'
        if include_cover
        else ""
    )

    opf_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
        <dc:title>{title}</dc:title>
        <dc:creator>{author}</dc:creator>
        {overview_tag}
        {isbn_tag}
        <dc:language>en</dc:language>
        <dc:publisher>Chilton Books</dc:publisher>
        <dc:date>1965-08-01</dc:date>
        {series_meta}
    </metadata>
    <manifest>
        <item id="chapter1" href="chap1.xhtml" media-type="application/xhtml+xml"/>
        {cover_item}
    </manifest>
    <spine>
        <itemref idref="chapter1"/>
    </spine>
</package>"""

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container_xml)
        zf.writestr("OEBPS/content.opf", opf_xml)
        zf.writestr(
            "OEBPS/chap1.xhtml", "<html><body><p>Beginnings are delicate.</p></body></html>"
        )
        if cover_bytes:
            zf.writestr("OEBPS/images/cover.jpg", cover_bytes)


def _create_minimal_cbz(
    path: Path,
    title: str = "Issue 1",
    series: str = "Watchmen",
    number: str = "1",
    writer: str = "Alan Moore",
    page_count: int = 3,
) -> None:
    comic_info = f"""<?xml version="1.0"?>
<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema"
           xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Title>{title}</Title>
  <Series>{series}</Series>
  <Number>{number}</Number>
  <Writer>{writer}</Writer>
  <Summary>Who watches the watchmen?</Summary>
  <Year>1986</Year>
  <PageCount>{page_count}</PageCount>
</ComicInfo>"""

    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ComicInfo.xml", comic_info)
        for i in range(1, page_count + 1):
            zf.writestr(f"page_{i:03d}.jpg", _create_test_image(color="green"))


def _create_minimal_pdf(
    path: Path,
    title: str = "The Art of War",
    author: str = "Sun Tzu",
    subject: str = "Strategy",
    pages: int = 2,
) -> None:
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=300)
    writer.add_metadata(
        {
            "/Title": title,
            "/Author": author,
            "/Subject": subject,
        }
    )
    with open(path, "wb") as f:
        writer.write(f)


def test_read_epub_metadata(tmp_path: Path) -> None:
    epub_file = tmp_path / "dune.epub"
    _create_minimal_epub(epub_file)

    meta = read_epub_metadata(epub_file)
    assert meta.title == "Dune"
    assert meta.author == "Frank Herbert"
    assert meta.series == "Dune Chronicles"
    assert meta.series_index == 1.0
    assert meta.isbn == "9780441172719"
    assert meta.publisher == "Chilton Books"
    assert meta.published_date == "1965-08-01"
    assert meta.overview == "A science fiction masterpiece."
    assert meta.cover_data is not None
    assert meta.cover_mime == "image/jpeg"


def test_read_cbz_metadata_and_pages(tmp_path: Path) -> None:
    cbz_file = tmp_path / "watchmen_01.cbz"
    _create_minimal_cbz(cbz_file, page_count=3)

    meta = read_cbz_metadata(cbz_file)
    assert meta.title == "Issue 1"
    assert meta.series == "Watchmen"
    assert meta.series_index == 1.0
    assert meta.author == "Alan Moore"
    assert meta.page_count == 3
    assert meta.overview == "Who watches the watchmen?"
    assert meta.cover_data is not None

    # Test extracting pages
    p1_data, p1_mime = extract_comic_page(cbz_file, 1)
    assert len(p1_data) > 0
    assert p1_mime == "image/jpeg"

    p3_data, p3_mime = extract_comic_page(cbz_file, 3)
    assert len(p3_data) > 0

    with pytest.raises(IndexError):
        extract_comic_page(cbz_file, 0)

    with pytest.raises(IndexError):
        extract_comic_page(cbz_file, 4)


def test_read_pdf_metadata(tmp_path: Path) -> None:
    pdf_file = tmp_path / "art_of_war.pdf"
    _create_minimal_pdf(pdf_file, pages=2)

    meta = read_pdf_metadata(pdf_file)
    assert meta.title == "The Art of War"
    assert meta.author == "Sun Tzu"
    assert meta.overview == "Strategy"
    assert meta.page_count == 2
    assert meta.cover_data is not None
    assert meta.cover_mime == "image/jpeg"


def test_unified_reader_dispatch(tmp_path: Path) -> None:
    epub = tmp_path / "test.epub"
    _create_minimal_epub(epub)
    cbz = tmp_path / "test.cbz"
    _create_minimal_cbz(cbz)
    pdf = tmp_path / "test.pdf"
    _create_minimal_pdf(pdf)

    assert read_book_metadata(epub).title == "Dune"
    assert read_book_metadata(cbz).series == "Watchmen"
    assert read_book_metadata(pdf).author == "Sun Tzu"

    # Unsupported / fallback
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    res = read_book_metadata(txt)
    assert res.title == "notes"
