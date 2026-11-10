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
from app.core.plugins.storage import (
    PersistedPluginState,
    load_all_plugins_state,
    load_plugin_state,
    mask_plugin_config,
    mask_secret,
    save_plugin_state,
)

__all__ = [
    "ApiAdapterPlugin",
    "BasePlugin",
    "ManifestError",
    "MetadataPlugin",
    "NotificationPlugin",
    "PersistedPluginState",
    "PluginCapability",
    "PluginContext",
    "PluginManifest",
    "PluginStatus",
    "ScrobblerPlugin",
    "ServerEvent",
    "load_all_plugins_state",
    "load_manifest",
    "load_plugin_state",
    "mask_plugin_config",
    "mask_secret",
    "parse_manifest_data",
    "save_plugin_state",
]
