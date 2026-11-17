"""Wire metadata providers, the matcher and the artwork pipeline together.

Keeping construction in one place means the scan job handler, the manual match
endpoint and any future caller all get the same matcher, including the image
pipeline as its artwork callback.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.plugins.base import MetadataPlugin, PluginStatus
from app.core.plugins.manager import PluginManager
from app.services.images.pipeline import ImagePipeline
from app.services.metadata.local import LocalProvider
from app.services.metadata.matching import MetadataMatcher
from app.services.metadata.registry import MetadataRegistry


def build_metadata_matcher(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    plugin_manager: PluginManager,
    registry: MetadataRegistry | None = None,
) -> MetadataMatcher:
    """Build a matcher backed by the active metadata plugins."""
    artwork = ImagePipeline(settings, sessionmaker)
    if registry is None:
        registry = MetadataRegistry(sessionmaker=sessionmaker)
        registry.register(LocalProvider())
        for plugin in plugin_manager.list_plugins():
            if isinstance(plugin, MetadataPlugin) and plugin.status == PluginStatus.ACTIVE:
                registry.register(plugin, supported_types=plugin.supported_media_types)
    return MetadataMatcher(settings, sessionmaker, artwork=artwork, registry=registry)
