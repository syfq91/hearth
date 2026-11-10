"""Hearth plugin system core models and interfaces."""

from app.core.plugins.manifest import (
    ManifestError,
    PluginCapability,
    PluginManifest,
    load_manifest,
    parse_manifest_data,
)

__all__ = [
    "ManifestError",
    "PluginCapability",
    "PluginManifest",
    "load_manifest",
    "parse_manifest_data",
]
