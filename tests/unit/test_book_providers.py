"""Unit tests for book metadata providers: Open Library, Google Books, and Local OPF."""

from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.services.metadata.base import BookMetadata
from app.services.metadata.googlebooks import GoogleBooksProvider
from app.services.metadata.local import (
    find_local_book_cover,
    find_local_book_opf,
    parse_local_opf,
)
from app.services.metadata.openlibrary import OpenLibraryProvider


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        env="testing",
        data_dir=tmp_path,
        db_path=tmp_path / "hearth.db",
        media_dir=tmp_path / "media",
    )


@pytest.mark.asyncio
async def test_openlibrary_search_and_details(settings: Settings) -> None:
    search_response = {
        "numFound": 1,
        "docs": [
            {
                "key": "/works/OL102749W",
                "title": "Dune",
                "author_name": ["Frank Herbert"],
                "first_publish_year": 1965,
                "cover_i": 5013444,
                "isbn": ["9780441172719"],
            }
        ],
    }
    work_response = {
        "key": "/works/OL102749W",
        "title": "Dune",
        "description": {"value": "Set on the desert planet Arrakis..."},
        "covers": [5013444],
        "subjects": ["Science Fiction", "Space Opera"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/search.json" in url:
            return httpx.Response(200, json=search_response)
        elif "/works/OL102749W.json" in url or "OL102749W" in url:
            return httpx.Response(200, json=work_response)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OpenLibraryProvider(settings, client=client)

    results = await provider.search_book("Dune", author="Frank Herbert")
    assert len(results) == 1
    assert results[0].title == "Dune"
    assert results[0].year == 1965
    assert results[0].provider_id == "/works/OL102749W"
    assert "5013444" in (results[0].poster_url or "")

    details = await provider.fetch_book_details("/works/OL102749W")
    assert details is not None
    assert details.title == "Dune"
    assert "Arrakis" in (details.overview or "")
    assert len(details.images) == 1
    assert "5013444" in (details.images[0].source_url or "")

    await provider.close()


@pytest.mark.asyncio
async def test_googlebooks_search_and_details(settings: Settings) -> None:
    volume_item = {
        "id": "B1R1CwAAQBAJ",
        "volumeInfo": {
            "title": "Dune",
            "authors": ["Frank Herbert"],
            "publisher": "Penguin",
            "publishedDate": "1965",
            "description": "Frank Herbert's classic masterpiece.",
            "pageCount": 412,
            "categories": ["Fiction / Science Fiction"],
            "imageLinks": {
                "thumbnail": "https://books.google.com/books/content?id=B1R1CwAAQBAJ&printsec=frontcover&img=1"
            },
        },
    }
    search_response = {"totalItems": 1, "items": [volume_item]}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "volumes?" in url:
            return httpx.Response(200, json=search_response)
        elif "volumes/B1R1CwAAQBAJ" in url:
            return httpx.Response(200, json=volume_item)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = GoogleBooksProvider(settings, client=client)

    results = await provider.search_book("Dune")
    assert len(results) == 1
    assert results[0].title == "Dune"
    assert results[0].provider_id == "B1R1CwAAQBAJ"

    details = await provider.fetch_book_details("B1R1CwAAQBAJ")
    assert details is not None
    assert details.title == "Dune"
    assert details.author_name == "Frank Herbert"
    assert details.publisher == "Penguin"
    assert details.page_count == 412
    assert len(details.images) == 1

    await provider.close()


def test_local_book_opf_and_cover(tmp_path: Path) -> None:
    book_dir = tmp_path / "Dune"
    book_dir.mkdir()

    opf_content = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
        <dc:title>Dune Local</dc:title>
        <dc:creator opf:role="aut">Frank Herbert</dc:creator>
        <dc:description>Local description</dc:description>
        <dc:publisher>Chilton</dc:publisher>
        <dc:date>1965-08-01</dc:date>
        <dc:language>en</dc:language>
        <dc:identifier opf:scheme="ISBN">9780441172719</dc:identifier>
        <meta name="calibre:series" content="Dune Chronicles"/>
        <meta name="calibre:series_index" content="1.0"/>
    </metadata>
</package>"""
    (book_dir / "metadata.opf").write_text(opf_content, encoding="utf-8")
    (book_dir / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32)

    found_opf = find_local_book_opf(book_dir)
    assert found_opf is not None
    assert found_opf.name == "metadata.opf"

    found_cover = find_local_book_cover(book_dir)
    assert found_cover is not None
    assert found_cover.name == "cover.jpg"

    meta = parse_local_opf(found_opf, cover_path=found_cover)
    assert isinstance(meta, BookMetadata)
    assert meta.provider == "local"
    assert meta.title == "Dune Local"
    assert meta.author_name == "Frank Herbert"
    assert meta.series_name == "Dune Chronicles"
    assert meta.series_index == 1.0
    assert meta.isbn == "9780441172719"
    assert meta.overview == "Local description"
    assert len(meta.images) == 1
    assert meta.images[0].file_path == str(found_cover)
    assert meta.images[0].is_primary is True
