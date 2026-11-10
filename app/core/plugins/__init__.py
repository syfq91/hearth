"""Hearth plugin system core models and interfaces."""

from app.core.plugins.base import (
    ApiAdapterPlugin,
    BasePlugin,
    MetadataPlugin,
    NotificationPlugin,
    PluginContext,
    PluginStatus,
    ScrobblerPlugin,
    ServerEvent,
)
from app.core.plugins.manifest import (
    ManifestError,
    PluginCapability,
    PluginManifest,
    load_manifest,
    parse_manifest_data,
)

__all__ = [
    "ApiAdapterPlugin",
    "BasePlugin",
    "ManifestError",
    "MetadataPlugin",
    "NotificationPlugin",
    "PluginCapability",
    "PluginContext",
    "PluginManifest",
    "PluginStatus",
    "ScrobblerPlugin",
    "ServerEvent",
    "load_manifest",
    "parse_manifest_data",
]
