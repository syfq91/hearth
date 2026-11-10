"""Plugin manager responsible for discovery, dynamic loading, and lifecycle management."""

from __future__ import annotations

import contextlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import __version__ as HEARTH_VERSION
from app.core.config import Settings
from app.core.plugins.base import BasePlugin, PluginContext, PluginStatus
from app.core.plugins.manifest import ManifestError, PluginManifest, load_manifest
from app.core.plugins.storage import (
    load_all_plugins_state,
    save_plugin_state,
)

logger = logging.getLogger("hearth.plugins.manager")


def _is_compatible(min_version: str, current_version: str = HEARTH_VERSION) -> bool:
    """Check if current Hearth version satisfies min_version requirement."""
    try:
        min_parts = [int(p) for p in min_version.split("-")[0].split(".")[:3]]
        cur_parts = [int(p) for p in current_version.split("-")[0].split(".")[:3]]
        return cur_parts >= min_parts
    except (ValueError, IndexError):
        return True


class PluginManager:
    """Manages plugin discovery, isolated dynamic loading, configuration, and state."""

    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self._plugins: dict[str, BasePlugin[Any]] = {}
        self._plugin_paths: dict[str, Path] = {}

    @property
    def plugins(self) -> dict[str, BasePlugin[Any]]:
        """Dictionary of loaded plugins keyed by unique plugin ID."""
        return self._plugins

    def get_plugin(self, plugin_id: str) -> BasePlugin[Any] | None:
        """Return the plugin instance for a given ID, or None if not found."""
        return self._plugins.get(plugin_id)

    def list_plugins(self) -> list[BasePlugin[Any]]:
        """Return list of all registered plugin instances."""
        return list(self._plugins.values())

    def get_plugins_with_capability(self, capability: str) -> list[BasePlugin[Any]]:
        """Return list of active plugins declaring a specific capability."""
        return [
            p
            for p in self._plugins.values()
            if p.status == PluginStatus.ACTIVE and capability in p.manifest.capabilities
        ]

    async def discover_and_load(self) -> list[BasePlugin[Any]]:
        """Discover and load all plugins from built-in and user plugin directories."""
        search_dirs: list[Path] = []
        if self.settings.core_plugins_dir and self.settings.core_plugins_dir.is_dir():
            search_dirs.append(self.settings.core_plugins_dir)
        if self.settings.user_plugins_dir and self.settings.user_plugins_dir.is_dir():
            search_dirs.append(self.settings.user_plugins_dir)

        # 1. Discover all plugin directories containing a plugin.json
        discovered_manifests: dict[str, tuple[PluginManifest, Path]] = {}
        for search_root in search_dirs:
            try:
                for entry in search_root.iterdir():
                    if entry.is_dir():
                        manifest_file = entry / "plugin.json"
                        if manifest_file.is_file():
                            try:
                                manifest = load_manifest(manifest_file)
                                # User plugins can override built-in plugins with same ID
                                discovered_manifests[manifest.id] = (manifest, entry)
                            except ManifestError as exc:
                                logger.error(
                                    "Failed to parse plugin manifest",
                                    extra={"path": str(manifest_file), "error": str(exc)},
                                )
            except OSError as exc:
                logger.warning(
                    "Error reading plugin search directory",
                    extra={"directory": str(search_root), "error": str(exc)},
                )

        # 2. Load stored plugin states from database
        persisted_states = await load_all_plugins_state(self.sessionmaker)

        # 3. Instantiate and initialize discovered plugins
        for plugin_id, (manifest, plugin_dir) in discovered_manifests.items():
            self._plugin_paths[plugin_id] = plugin_dir
            state = persisted_states.get(plugin_id)
            enabled = state.enabled if state is not None else True
            config = state.config if state is not None and state.config is not None else {}

            plugin = self._load_plugin_instance(manifest, plugin_dir)
            self._plugins[plugin_id] = plugin

            if not enabled:
                plugin.status = PluginStatus.DISABLED
                continue

            if not _is_compatible(manifest.min_hearth_version):
                plugin.status = PluginStatus.FAILED
                plugin.error_message = (
                    f"Incompatible with Hearth {HEARTH_VERSION} "
                    f"(requires >= {manifest.min_hearth_version})"
                )
                continue

            # Apply stored configuration
            try:
                plugin.apply_configuration(config)
            except Exception as exc:
                logger.error(
                    "Plugin configuration error",
                    extra={"plugin_id": plugin_id, "error": str(exc)},
                )
                plugin.status = PluginStatus.FAILED
                plugin.error_message = f"Configuration error: {exc}"
                continue

            # Check if required configuration is missing
            if manifest.config_schema and isinstance(manifest.config_schema, dict):
                required_fields = manifest.config_schema.get("required", [])
                active_config: dict[str, Any] = {}
                if plugin.config is not None and hasattr(plugin.config, "model_dump"):
                    active_config = plugin.config.model_dump()
                else:
                    active_config = config

                if any(
                    not active_config.get(field) or str(active_config.get(field, "")).strip() == ""
                    for field in required_fields
                ):
                    plugin.status = PluginStatus.CONFIG_REQUIRED

            # If still active, run async initialize hook
            if plugin.status == PluginStatus.ACTIVE:
                try:
                    await plugin.initialize()
                except Exception as exc:
                    logger.error(
                        "Plugin initialization failed",
                        extra={"plugin_id": plugin_id, "error": str(exc)},
                    )
                    plugin.status = PluginStatus.FAILED
                    plugin.error_message = f"Initialization failed: {exc}"

        logger.info(
            "Plugins discovery complete",
            extra={
                "discovered": len(self._plugins),
                "active": len(
                    [p for p in self._plugins.values() if p.status == PluginStatus.ACTIVE]
                ),
            },
        )
        return list(self._plugins.values())

    def _load_plugin_instance(self, manifest: PluginManifest, plugin_dir: Path) -> BasePlugin[Any]:
        """Dynamically import and instantiate a plugin with fault isolation."""
        # 1. Setup Python path for vendored dependencies if present (Decision 4A)
        vendor_dir = plugin_dir / "vendor"
        if vendor_dir.is_dir() and str(vendor_dir) not in sys.path:
            sys.path.insert(0, str(vendor_dir))

        if str(plugin_dir) not in sys.path:
            sys.path.insert(0, str(plugin_dir))

        # 2. Build runtime context
        plugin_data_dir = self.settings.data_dir / "plugins" / manifest.id
        with contextlib.suppress(OSError):
            plugin_data_dir.mkdir(parents=True, exist_ok=True)

        plugin_logger = logging.getLogger(f"hearth.plugins.{manifest.id}")
        context = PluginContext(
            settings=self.settings,
            sessionmaker=self.sessionmaker,
            logger=plugin_logger,
            data_dir=plugin_data_dir,
            plugin_dir=plugin_dir,
        )

        # 3. Resolve entrypoint
        module_name, class_name = manifest.entrypoint.split(":", 1)
        candidate_file = plugin_dir / f"{module_name}.py"
        if not candidate_file.is_file():
            candidate_file = plugin_dir / module_name / "__init__.py"

        if not candidate_file.is_file():
            plugin = _FailedPlugin(
                context,
                manifest,
                error=f"Entrypoint file {candidate_file.name} not found in {plugin_dir}",
            )
            return plugin

        # 4. Import module dynamically
        spec_name = f"hearth_plugin_{manifest.id.replace('.', '_')}_{module_name}"
        try:
            spec = importlib.util.spec_from_file_location(spec_name, candidate_file)
            if spec is None or spec.loader is None:
                return _FailedPlugin(
                    context, manifest, error=f"Could not load import spec for {candidate_file}"
                )

            module = importlib.util.module_from_spec(spec)
            sys.modules[spec_name] = module
            spec.loader.exec_module(module)

            plugin_class = getattr(module, class_name, None)
            if plugin_class is None:
                return _FailedPlugin(
                    context,
                    manifest,
                    error=f"Class {class_name!r} not found in module {candidate_file}",
                )

            instance = plugin_class(context, manifest)
            if not isinstance(instance, BasePlugin):
                return _FailedPlugin(
                    context,
                    manifest,
                    error=f"Class {class_name!r} does not inherit from BasePlugin",
                )

            return instance
        except Exception as exc:
            logger.error(
                "Error importing plugin",
                extra={"plugin_id": manifest.id, "error": str(exc)},
            )
            return _FailedPlugin(context, manifest, error=f"Import failed: {exc}")

    async def enable_plugin(self, plugin_id: str) -> BasePlugin[Any]:
        """Enable an inactive or disabled plugin."""
        plugin = self.get_plugin(plugin_id)
        if plugin is None:
            raise KeyError(f"Plugin {plugin_id!r} not found")

        await save_plugin_state(self.sessionmaker, plugin_id, enabled=True)

        if plugin.status == PluginStatus.DISABLED:
            plugin.status = PluginStatus.ACTIVE
            plugin.error_message = None
            try:
                await plugin.initialize()
            except Exception as exc:
                plugin.status = PluginStatus.FAILED
                plugin.error_message = f"Initialization failed: {exc}"

        return plugin

    async def disable_plugin(self, plugin_id: str) -> BasePlugin[Any]:
        """Disable an active plugin."""
        plugin = self.get_plugin(plugin_id)
        if plugin is None:
            raise KeyError(f"Plugin {plugin_id!r} not found")

        await save_plugin_state(self.sessionmaker, plugin_id, enabled=False)

        try:
            await plugin.dispose()
        except Exception as exc:
            logger.warning(
                "Error during plugin dispose",
                extra={"plugin_id": plugin_id, "error": str(exc)},
            )

        plugin.status = PluginStatus.DISABLED
        return plugin

    async def configure_plugin(
        self,
        plugin_id: str,
        config: dict[str, Any],
    ) -> BasePlugin[Any]:
        """Update and persist configuration for a plugin."""
        plugin = self.get_plugin(plugin_id)
        if plugin is None:
            raise KeyError(f"Plugin {plugin_id!r} not found")

        persisted = await save_plugin_state(self.sessionmaker, plugin_id, config=config)

        # Apply configuration to instance
        try:
            plugin.apply_configuration(persisted.config or {})
            if plugin.status == PluginStatus.CONFIG_REQUIRED:
                plugin.status = PluginStatus.ACTIVE
                plugin.error_message = None
                await plugin.initialize()
        except Exception as exc:
            plugin.status = PluginStatus.FAILED
            plugin.error_message = f"Configuration error: {exc}"

        return plugin

    async def dispose_all(self) -> None:
        """Teardown all active plugins upon server shutdown."""
        for plugin in self._plugins.values():
            if plugin.status == PluginStatus.ACTIVE:
                try:
                    await plugin.dispose()
                except Exception as exc:
                    logger.warning(
                        "Error disposing plugin during shutdown",
                        extra={"plugin_id": plugin.manifest.id, "error": str(exc)},
                    )


class _FailedPlugin(BasePlugin[Any]):
    """Stand-in plugin representation for a plugin that failed to load."""

    def __init__(self, context: PluginContext, manifest: PluginManifest, error: str) -> None:
        super().__init__(context, manifest)
        self.status = PluginStatus.FAILED
        self.error_message = error
