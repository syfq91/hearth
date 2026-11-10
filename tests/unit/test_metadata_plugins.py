"""Integration and unit tests for discovering and using metadata plugins."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.plugins.base import MetadataPlugin, PluginStatus
from app.core.plugins.manager import PluginManager
from app.services.metadata.registry import MetadataRegistry


async def test_discover_and_load_builtin_metadata_plugins(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    """Verify PluginManager discovers all 4 built-in plugins from repo plugins/."""
    # Configure db_settings with test API keys and flags
    db_settings.tmdb_api_key = "test-env-tmdb-key"
    db_settings.google_books_api_key = "test-env-gb-key"
    db_settings.musicbrainz_enabled = True
    db_settings.openlibrary_enabled = True
    db_settings.google_books_enabled = True

    # Builtin plugins directory is the repository's plugins/ directory
    repo_plugins_dir = Path(__file__).resolve().parent.parent.parent / "plugins"
    db_settings.builtin_plugins_dir = repo_plugins_dir
    db_settings.plugins_dir = tmp_path / "user_plugins"

    manager = PluginManager(db_settings, db_sessionmaker)
    plugins = await manager.discover_and_load()

    assert len(plugins) >= 4
    plugin_ids = {p.manifest.id for p in plugins}
    assert "org.hearth.plugins.tmdb" in plugin_ids
    assert "org.hearth.plugins.musicbrainz" in plugin_ids
    assert "org.hearth.plugins.openlibrary" in plugin_ids
    assert "org.hearth.plugins.googlebooks" in plugin_ids

    # TMDB plugin checks
    tmdb = manager.get_plugin("org.hearth.plugins.tmdb")
    assert tmdb is not None
    assert isinstance(tmdb, MetadataPlugin)
    assert tmdb.status == PluginStatus.ACTIVE
    assert "movie" in tmdb.supported_media_types
    assert "series" in tmdb.supported_media_types
    # Fallback to env setting
    assert tmdb.config is not None
    assert tmdb.config.api_key == "test-env-tmdb-key"

    # MusicBrainz plugin checks
    mb = manager.get_plugin("org.hearth.plugins.musicbrainz")
    assert mb is not None
    assert isinstance(mb, MetadataPlugin)
    assert mb.status == PluginStatus.ACTIVE
    assert "artist" in mb.supported_media_types
    assert "album" in mb.supported_media_types

    # Open Library plugin checks
    ol = manager.get_plugin("org.hearth.plugins.openlibrary")
    assert ol is not None
    assert isinstance(ol, MetadataPlugin)
    assert ol.status == PluginStatus.ACTIVE
    assert "book" in ol.supported_media_types

    # Google Books plugin checks
    gb = manager.get_plugin("org.hearth.plugins.googlebooks")
    assert gb is not None
    assert isinstance(gb, MetadataPlugin)
    assert gb.status == PluginStatus.ACTIVE
    assert "book" in gb.supported_media_types
    assert gb.config is not None
    assert gb.config.api_key == "test-env-gb-key"


async def test_metadata_plugins_integration_with_registry(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    """Verify registering discovered metadata plugins into MetadataRegistry."""
    db_settings.tmdb_api_key = "test-tmdb-key"
    repo_plugins_dir = Path(__file__).resolve().parent.parent.parent / "plugins"
    db_settings.builtin_plugins_dir = repo_plugins_dir
    db_settings.plugins_dir = tmp_path / "user_plugins"

    manager = PluginManager(db_settings, db_sessionmaker)
    await manager.discover_and_load()

    registry = MetadataRegistry(sessionmaker=db_sessionmaker)

    for plugin in manager.list_plugins():
        if isinstance(plugin, MetadataPlugin) and plugin.status == PluginStatus.ACTIVE:
            registry.register(plugin, supported_types=plugin.supported_media_types)

    # Movie & TV providers
    movie_providers = registry.get_providers_for("movie")
    assert any(p.name == "tmdb" for p in movie_providers)

    series_providers = registry.get_providers_for("series")
    assert any(p.name == "tmdb" for p in series_providers)

    # Music providers
    music_providers = registry.get_providers_for("music")
    assert any(p.name == "musicbrainz" for p in music_providers)

    # Book providers
    book_providers = registry.get_providers_for("book")
    book_names = [p.name for p in book_providers]
    assert "openlibrary" in book_names
    assert "googlebooks" in book_names

    # Custom priority order works with plugin-based providers
    reordered_books = registry.get_providers_for(
        "book", priority_order=["googlebooks", "openlibrary"]
    )
    reordered_names = [p.name for p in reordered_books]
    assert reordered_names == ["googlebooks", "openlibrary"]


async def test_disable_and_enable_metadata_plugin(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    """Verify runtime disabling and enabling of a metadata plugin."""
    repo_plugins_dir = Path(__file__).resolve().parent.parent.parent / "plugins"
    db_settings.builtin_plugins_dir = repo_plugins_dir
    db_settings.plugins_dir = tmp_path / "user_plugins"

    manager = PluginManager(db_settings, db_sessionmaker)
    await manager.discover_and_load()

    registry = MetadataRegistry(sessionmaker=db_sessionmaker)
    for p in manager.list_plugins():
        if isinstance(p, MetadataPlugin) and p.status == PluginStatus.ACTIVE:
            registry.register(p, supported_types=p.supported_media_types)

    assert any(p.name == "googlebooks" for p in registry.get_providers_for("book"))

    # Disable Google Books plugin
    gb = await manager.disable_plugin("org.hearth.plugins.googlebooks")
    assert gb.status == PluginStatus.DISABLED
    assert not gb.enabled

    # Disabled provider should not appear in get_providers_for
    assert not any(p.name == "googlebooks" for p in registry.get_providers_for("book"))

    # Re-enable
    await manager.enable_plugin("org.hearth.plugins.googlebooks")
    assert gb.status == PluginStatus.ACTIVE
    assert gb.enabled
    assert any(p.name == "googlebooks" for p in registry.get_providers_for("book"))
