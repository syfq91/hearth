"""Unit tests for MetadataRegistry and decoupled MetadataMatcher."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Book, Library, MediaFile, MediaItem
from app.services.metadata.base import (
    BookMetadata,
    MetadataProvider,
    MovieMetadata,
    ProviderImage,
    SearchResult,
)
from app.services.metadata.matching import MetadataMatcher
from app.services.metadata.registry import (
    MetadataRegistry,
    load_library_provider_order,
    save_library_provider_order,
)


class DummyProvider(MetadataProvider):
    def __init__(
        self,
        name: str,
        *,
        enabled: bool = True,
        supported_types: tuple[str, ...] = ("movie", "book"),
    ) -> None:
        self.name = name
        self._enabled = enabled
        self.supported_media_types = supported_types
        self.calls: list[str] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        self.calls.append(f"search:{query}")
        return [SearchResult(provider=self.name, provider_id="123", title=query, year=year)]

    async def fetch_details(self, provider_id: str) -> MovieMetadata | None:
        self.calls.append(f"details:{provider_id}")
        return MovieMetadata(
            provider=self.name,
            provider_id=provider_id,
            title="Dummy Movie",
            year=2024,
        )

    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        return []

    async def search_book(
        self, query: str, author: str | None = None, isbn: str | None = None
    ) -> list[SearchResult]:
        self.calls.append(f"search_book:{query}")
        return [SearchResult(provider=self.name, provider_id=f"b_{self.name}", title=query)]

    async def fetch_book_details(self, book_id: str) -> BookMetadata | None:
        self.calls.append(f"fetch_book_details:{book_id}")
        return BookMetadata(
            provider=self.name,
            provider_id=book_id,
            title=f"Book from {self.name}",
        )


def test_registry_registration_and_types() -> None:
    registry = MetadataRegistry()
    p1 = DummyProvider("provider_a", supported_types=("movie", "series"))
    p2 = DummyProvider("provider_b", supported_types=("book",))
    disabled = DummyProvider("disabled_p", enabled=False, supported_types=("movie",))

    registry.register(p1)
    registry.register(p2)
    registry.register(disabled)

    assert registry.get_provider("provider_a") is p1
    assert registry.get_provider("provider_b") is p2
    assert len(registry.all_providers()) == 3

    # Check media type query: disabled should be excluded
    movie_providers = registry.get_providers_for("movie")
    assert movie_providers == [p1]

    # "tv" should map to "series"
    tv_providers = registry.get_providers_for("tv")
    assert tv_providers == [p1]

    book_providers = registry.get_providers_for("book")
    assert book_providers == [p2]

    # Unregister
    assert registry.unregister("provider_a") is p1
    assert registry.get_provider("provider_a") is None
    assert registry.get_providers_for("movie") == []


def test_registry_priority_sorting() -> None:
    registry = MetadataRegistry()
    p1 = DummyProvider("p1", supported_types=("book",))
    p2 = DummyProvider("p2", supported_types=("book",))
    p3 = DummyProvider("p3", supported_types=("book",))

    registry.register(p1)
    registry.register(p2)
    registry.register(p3)

    # Default order
    assert [p.name for p in registry.get_providers_for("book")] == ["p1", "p2", "p3"]

    # Explicit custom order
    reordered = registry.get_providers_for("book", priority_order=["p3", "p1"])
    assert [p.name for p in reordered] == ["p3", "p1", "p2"]


async def test_registry_library_level_priority(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    registry = MetadataRegistry(sessionmaker=db_sessionmaker)
    p_openlibrary = DummyProvider("openlibrary", supported_types=("book",))
    p_googlebooks = DummyProvider("googlebooks", supported_types=("book",))

    registry.register(p_openlibrary)
    registry.register(p_googlebooks)

    # Save library order for manga and novels
    manga_lib_id = "lib_manga"
    novels_lib_id = "lib_novels"

    await save_library_provider_order(db_sessionmaker, manga_lib_id, ["googlebooks", "openlibrary"])
    await save_library_provider_order(
        db_sessionmaker, novels_lib_id, ["openlibrary", "googlebooks"]
    )

    # Load and verify
    manga_order = await load_library_provider_order(db_sessionmaker, manga_lib_id)
    assert manga_order == ["googlebooks", "openlibrary"]

    # Prime cache in registry
    await registry.prime_library_orders()

    manga_providers = registry.get_providers_for("book", library_id=manga_lib_id)
    assert [p.name for p in manga_providers] == ["googlebooks", "openlibrary"]

    novels_providers = registry.get_providers_for("book", library_id=novels_lib_id)
    assert [p.name for p in novels_providers] == ["openlibrary", "googlebooks"]

    # Clear library order
    await save_library_provider_order(db_sessionmaker, manga_lib_id, None)
    cleared = await load_library_provider_order(db_sessionmaker, manga_lib_id)
    assert cleared is None


async def test_matcher_with_registry_per_library_ordering(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    registry = MetadataRegistry(sessionmaker=db_sessionmaker)
    p_first = DummyProvider("p_first", supported_types=("book",))
    p_second = DummyProvider("p_second", supported_types=("book",))

    registry.register(p_first)
    registry.register(p_second)

    lib_id = "custom_book_library"
    # Configure library to prefer p_second over p_first
    await save_library_provider_order(db_sessionmaker, lib_id, ["p_second", "p_first"])

    matcher = MetadataMatcher(db_settings, db_sessionmaker, registry=registry)

    # Seed library and book item
    async with db_sessionmaker() as session:
        library = Library(id=lib_id, name="Book Lib", type="book")
        session.add(library)

        item = MediaItem(
            id="book_item_1",
            library_id=lib_id,
            title="Testing Book",
            sort_title="testing book",
            type="book",
            match_state="pending",
        )
        item.book = Book()
        session.add(item)
        file = MediaFile(
            id="book_file_1",
            item_id="book_item_1",
            path=str(tmp_path / "test.epub"),
            size_bytes=1000,
            mtime=1.0,
            partial_hash="dummy_hash",
        )
        session.add(file)
        await session.commit()

    # Match item
    result = await matcher.match_item("book_item_1")
    assert result.state == "matched"
    # p_second was preferred by the library, so it should have won!
    assert result.provider == "p_second"
    assert "search_book:Testing Book" in p_second.calls
    # p_first should not have been called because p_second already matched
    assert len(p_first.calls) == 0
