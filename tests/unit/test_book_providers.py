"""Unit tests for book metadata providers: Open Library, Google Books, and Local OPF."""

from pathlib import Path

import httpx
import pytest

from app.core.config import Settings
from app.services.metadata.base import BookMetadata, ProviderRateLimited
from app.services.metadata.local import (
    find_local_book_cover,
    find_local_book_opf,
    find_local_comic_info,
    parse_local_comic_info,
    parse_local_opf,
)
from plugins.googlebooks.plugin import GoogleBooksPlugin
from plugins.openlibrary.plugin import OpenLibraryPlugin
from tests.support import make_metadata_plugin


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
    provider = make_metadata_plugin(OpenLibraryPlugin, settings=settings, client=client)

    results = await provider.search_book("Dune", author="Frank Herbert")
    assert len(results) == 1
    assert results[0].title == "Dune"
    assert results[0].year == 1965
    assert results[0].provider_id == "/works/OL102749W"
    assert results[0].source_url == "https://openlibrary.org/works/OL102749W"
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
    provider = make_metadata_plugin(GoogleBooksPlugin, settings=settings, client=client)

    results = await provider.search_book("Dune")
    assert len(results) == 1
    assert results[0].title == "Dune"
    assert results[0].provider_id == "B1R1CwAAQBAJ"
    assert results[0].source_url == "https://books.google.com/books?id=B1R1CwAAQBAJ"

    details = await provider.fetch_book_details("B1R1CwAAQBAJ")
    assert details is not None
    assert details.title == "Dune"
    assert details.author_name == "Frank Herbert"
    assert details.publisher == "Penguin"
    assert details.page_count == 412
    assert len(details.images) == 1

    await provider.close()


@pytest.mark.asyncio
async def test_googlebooks_search_uses_free_text_query(settings: Settings) -> None:
    urls: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(request.url)
        return httpx.Response(200, json={"totalItems": 0, "items": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = make_metadata_plugin(GoogleBooksPlugin, settings=settings, client=client)

    await provider.search_book("A Certain Scientific Railgun Vol. 1", author="Kazuma Kamachi")

    assert len(urls) == 1
    assert urls[0].params["q"] == ('A Certain Scientific Railgun Vol. 1 inauthor:"Kazuma Kamachi"')
    assert "intitle:" not in urls[0].params["q"]
    await provider.close()


@pytest.mark.asyncio
async def test_googlebooks_quota_failure_keeps_upstream_reason(settings: Settings) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "0"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = make_metadata_plugin(GoogleBooksPlugin, settings=settings, client=client)

    with pytest.raises(ProviderRateLimited, match="quota exceeded") as exc_info:
        await provider.fetch_book_details("EELNEQAAQBAJ")

    assert "HTTP 429" in str(exc_info.value)
    assert calls == 2  # one retry before giving up
    await provider.close()


@pytest.mark.asyncio
async def test_googlebooks_missing_volume_still_returns_none(settings: Settings) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404)))
    provider = make_metadata_plugin(GoogleBooksPlugin, settings=settings, client=client)

    assert await provider.fetch_book_details("MISSING") is None
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


def _write_opf(path: Path, title: str) -> None:
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f"<dc:title>{title}</dc:title>"
        "</metadata></package>",
        encoding="utf-8",
    )


def test_local_book_opf_prefers_book_stem(tmp_path: Path) -> None:
    book_dir = tmp_path / "Books"
    book_dir.mkdir()
    _write_opf(book_dir / "Dune.opf", "Dune Local")
    _write_opf(book_dir / "Foundation.opf", "Foundation Local")
    _write_opf(book_dir / "metadata.opf", "Shared Fallback")

    dune_opf = find_local_book_opf(book_dir, book_path=book_dir / "Dune.epub")
    foundation_opf = find_local_book_opf(book_dir, book_path=book_dir / "Foundation.epub")

    assert dune_opf is not None
    assert foundation_opf is not None
    assert dune_opf.name == "Dune.opf"
    assert foundation_opf.name == "Foundation.opf"
    assert parse_local_opf(dune_opf).title == "Dune Local"
    assert parse_local_opf(foundation_opf).title == "Foundation Local"


def test_local_book_opf_stem_match_is_case_insensitive(tmp_path: Path) -> None:
    book_dir = tmp_path / "Books"
    book_dir.mkdir()
    _write_opf(book_dir / "dune.opf", "Dune Local")

    found = find_local_book_opf(book_dir, book_path=book_dir / "Dune.epub")

    assert found is not None
    assert found.name == "dune.opf"


def test_local_book_opf_falls_back_when_no_stem_match(tmp_path: Path) -> None:
    book_dir = tmp_path / "Books"
    book_dir.mkdir()
    _write_opf(book_dir / "metadata.opf", "Shared Fallback")

    found = find_local_book_opf(book_dir, book_path=book_dir / "Dune.epub")

    assert found is not None
    assert found.name == "metadata.opf"


def test_local_book_opf_without_book_path_keeps_metadata_precedence(tmp_path: Path) -> None:
    book_dir = tmp_path / "Books"
    book_dir.mkdir()
    _write_opf(book_dir / "Dune.opf", "Dune Local")
    _write_opf(book_dir / "metadata.opf", "Shared Fallback")

    found = find_local_book_opf(book_dir)

    assert found is not None
    assert found.name == "metadata.opf"


def _write_comic_info(path: Path, title: str, number: str, writer: str) -> None:
    path.write_text(
        '<?xml version="1.0"?>'
        "<ComicInfo>"
        f"<Title>{title}</Title>"
        "<Series>Watchmen</Series>"
        f"<Number>{number}</Number>"
        f"<Writer>{writer}</Writer>"
        "<Year>1986</Year>"
        "<PageCount>2</PageCount>"
        "</ComicInfo>",
        encoding="utf-8",
    )


def test_local_comic_info_matches_comic_stem(tmp_path: Path) -> None:
    _write_comic_info(tmp_path / "Watchmen #1.xml", "Chapter One", "1", "Alan Moore")
    _write_comic_info(tmp_path / "Watchmen #2.xml", "Chapter Two", "2", "Dave Gibbons")

    first = find_local_comic_info(tmp_path / "Watchmen #1.cbz")
    second = find_local_comic_info(tmp_path / "Watchmen #2.cbz")

    assert first is not None
    assert second is not None
    assert first.name == "Watchmen #1.xml"
    assert second.name == "Watchmen #2.xml"

    first_meta = parse_local_comic_info(first)
    second_meta = parse_local_comic_info(second)
    assert first_meta.title == "Chapter One"
    assert first_meta.series_name == "Watchmen"
    assert first_meta.series_index == 1.0
    assert first_meta.author_name == "Alan Moore"
    assert first_meta.published_date == "1986"
    assert first_meta.page_count == 2
    assert second_meta.title == "Chapter Two"
    assert second_meta.series_index == 2.0
    assert second_meta.author_name == "Dave Gibbons"


def test_local_comic_info_is_case_insensitive_and_comic_only(tmp_path: Path) -> None:
    _write_comic_info(tmp_path / "watchmen #1.XML", "Chapter One", "1", "Alan Moore")

    found = find_local_comic_info(tmp_path / "Watchmen #1.CBZ")

    assert found is not None
    assert found.name == "watchmen #1.XML"
    assert parse_local_comic_info(found).title == "Chapter One"
    assert find_local_comic_info(tmp_path / "Dune.epub") is None


def test_local_comic_info_ignores_unrelated_xml(tmp_path: Path) -> None:
    (tmp_path / "Watchmen #1.xml").write_text(
        "<metadata><title>Not ComicInfo</title></metadata>", encoding="utf-8"
    )

    assert find_local_comic_info(tmp_path / "Watchmen #1.cbz") is None
