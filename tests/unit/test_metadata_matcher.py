"""Unit tests for MetadataMatcher integrating with MetadataRegistry and plugin providers."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.ids import new_id
from app.db.models import Library, LibraryPath, MediaFile, MediaItem, Movie
from app.services.metadata.base import (
    MetadataProvider,
    MovieMetadata,
    ProviderImage,
    SearchResult,
)
from app.services.metadata.local import LocalProvider
from app.services.metadata.matching import MetadataMatcher
from app.services.metadata.registry import (
    MetadataRegistry,
    save_library_provider_order,
)


class MockPluginProvider(MetadataProvider):
    """A mock metadata provider mimicking a plugin."""

    def __init__(
        self,
        name: str,
        *,
        enabled: bool = True,
        supported_types: tuple[str, ...] = ("movie",),
        priority_rank: int = 0,
    ) -> None:
        self.name = name
        self._enabled = enabled
        self.supported_media_types = supported_types
        self.priority_rank = priority_rank
        self.search_calls: list[str] = []
        self.details_calls: list[str] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        self.search_calls.append(query)
        self.last_query = query
        self.last_year = year
        if not self._enabled:
            return []
        return [
            SearchResult(
                provider=self.name,
                provider_id=f"{self.name}_123",
                title=query,
                year=year,
            )
        ]

    async def fetch_details(self, provider_id: str) -> MovieMetadata | None:
        self.details_calls.append(provider_id)
        return MovieMetadata(
            provider=self.name,
            provider_id=provider_id,
            title=getattr(self, "last_query", "Inception"),
            year=getattr(self, "last_year", 2010) or 2010,
            overview=f"Metadata retrieved from {self.name}",
        )

    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        return []


async def _seed_test_movie(
    sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
    *,
    title: str = "Inception",
    year: int = 2010,
) -> tuple[str, str]:
    """Seed a test library and movie item."""
    movie_file = tmp_path / "Inception.2010.mkv"
    movie_file.write_bytes(b"dummy video")

    lib_id = new_id()
    item_id = new_id()
    async with sessionmaker() as session:
        lib = Library(
            id=lib_id,
            name=f"Test Movies {lib_id[:8]}",
            type="movie",
        )
        session.add(lib)
        session.add(LibraryPath(id=new_id(), library_id=lib_id, path=str(tmp_path)))

        item = MediaItem(
            id=item_id,
            library_id=lib.id,
            type="movie",
            title=title,
            sort_title=title.lower(),
            year=year,
            match_state="pending",
        )
        item.movie = Movie()
        session.add(item)

        mf = MediaFile(
            id=new_id(),
            item_id=item.id,
            path=str(movie_file),
            size_bytes=100,
            mtime=1.0,
            partial_hash="hash1",
        )
        session.add(mf)
        await session.commit()
        return lib.id, item.id


async def test_matcher_queries_plugin_providers_via_registry(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    """Verify MetadataMatcher successfully resolves items using a plugin provider registered in
    MetadataRegistry.
    """
    lib_id, item_id = await _seed_test_movie(db_sessionmaker, tmp_path)

    registry = MetadataRegistry(sessionmaker=db_sessionmaker)
    plugin_provider = MockPluginProvider("mock_plugin")
    registry.register(LocalProvider())
    registry.register(plugin_provider)

    matcher = MetadataMatcher(db_settings, db_sessionmaker, registry=registry)
    stats = await matcher.match_pending()

    assert stats.matched == 1
    assert "Inception" in plugin_provider.search_calls
    assert "mock_plugin_123" in plugin_provider.details_calls

    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None
        assert item.match_state == "matched"
        assert item.movie is not None
        assert item.overview == "Metadata retrieved from mock_plugin"


async def test_matcher_respects_library_provider_priority(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    """Verify that library-specific provider priority determines which provider is queried first."""
    lib_id, item_id = await _seed_test_movie(db_sessionmaker, tmp_path)

    provider_a = MockPluginProvider("provider_alpha")
    provider_b = MockPluginProvider("provider_beta")

    # Set library priority: beta before alpha
    await save_library_provider_order(db_sessionmaker, lib_id, ["provider_beta", "provider_alpha"])

    registry = MetadataRegistry(sessionmaker=db_sessionmaker)
    registry.register(provider_a)
    registry.register(provider_b)

    matcher = MetadataMatcher(db_settings, db_sessionmaker, registry=registry)
    stats = await matcher.match_pending()

    assert stats.matched == 1
    # provider_beta should have been called first and matched
    assert "Inception" in provider_b.search_calls
    assert len(provider_a.search_calls) == 0

    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None
        assert item.match_state == "matched"
        assert item.movie is not None
        assert item.overview == "Metadata retrieved from provider_beta"


async def test_matcher_skips_disabled_plugin_provider(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    """Verify that disabling a provider prevents MetadataMatcher from invoking it."""
    lib_id, item_id = await _seed_test_movie(db_sessionmaker, tmp_path)

    disabled_plugin = MockPluginProvider("plugin_disabled", enabled=False)
    fallback_plugin = MockPluginProvider("plugin_fallback", enabled=True)

    registry = MetadataRegistry(sessionmaker=db_sessionmaker)
    registry.register(disabled_plugin)
    registry.register(fallback_plugin)

    matcher = MetadataMatcher(db_settings, db_sessionmaker, registry=registry)
    stats = await matcher.match_pending()

    assert stats.matched == 1
    # Disabled provider should not receive search calls
    assert len(disabled_plugin.search_calls) == 0
    # Fallback plugin matched
    assert "Inception" in fallback_plugin.search_calls

    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None
        assert item.match_state == "matched"
        assert item.movie is not None
        assert item.overview == "Metadata retrieved from plugin_fallback"


async def test_matcher_local_nfo_takes_precedence_in_default_flow(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    """Verify that local NFO metadata takes precedence over remote plugin queries in the
    standard pipeline.
    """
    # Write a local .nfo next to the movie file
    nfo_file = tmp_path / "Inception.2010.nfo"
    nfo_file.write_text(
        "<movie><title>Inception Local NFO</title><year>2010</year><plot>Local plot</plot></movie>",
        encoding="utf-8",
    )
    lib_id, item_id = await _seed_test_movie(db_sessionmaker, tmp_path)

    remote_plugin = MockPluginProvider("remote_plugin")
    registry = MetadataRegistry(sessionmaker=db_sessionmaker)
    registry.register(LocalProvider())
    registry.register(remote_plugin)

    matcher = MetadataMatcher(db_settings, db_sessionmaker, registry=registry)
    stats = await matcher.match_pending()

    assert stats.matched == 1
    # Local NFO matched first; remote plugin was never queried
    assert len(remote_plugin.search_calls) == 0

    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None
        assert item.match_state == "matched"
        assert item.title == "Inception Local NFO"
        assert "nfo" in item.provider_ids
