"""Plugin management API endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.core.deps import PluginManagerDep, RequireAdmin
from app.core.errors import NotFoundError
from app.core.plugins.base import BasePlugin, PluginStatus
from app.core.plugins.storage import (
    load_plugin_state,
    mask_plugin_config,
)
from app.db.session import SessionDep
from app.services.audit import record_audit

router = APIRouter(prefix="/plugins", tags=["plugins"])


class PluginSummary(BaseModel):
    """Summary of an installed plugin and its runtime status."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str
    description: str
    author: str
    category: str
    status: PluginStatus
    capabilities: list[str]
    error_message: str | None = None
    is_builtin: bool = True
    has_config_schema: bool = False
    is_configured: bool = True


class PluginDetail(BaseModel):
    """Detailed manifest and configuration for a single plugin."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str
    description: str
    author: str
    category: str
    min_hearth_version: str
    status: PluginStatus
    capabilities: list[str]
    error_message: str | None = None
    is_builtin: bool = True
    config_schema: dict[str, Any] | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class PluginConfigResponse(BaseModel):
    """Current configuration for a plugin with sensitive fields masked."""

    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    config: dict[str, Any]
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")


class UpdatePluginConfigRequest(BaseModel):
    """Payload to update configuration values for a plugin."""

    model_config = ConfigDict(extra="forbid")

    config: dict[str, Any]


def _build_summary(plugin: BasePlugin[Any], is_builtin: bool = True) -> PluginSummary:
    has_schema = bool(plugin.manifest.config_schema)
    is_configured = plugin.status != PluginStatus.CONFIG_REQUIRED
    return PluginSummary(
        id=plugin.manifest.id,
        name=plugin.manifest.name,
        version=plugin.manifest.version,
        description=plugin.manifest.description,
        author=plugin.manifest.author,
        category=plugin.manifest.category,
        status=plugin.status,
        capabilities=plugin.manifest.capabilities,
        error_message=plugin.error_message,
        is_builtin=is_builtin,
        has_config_schema=has_schema,
        is_configured=is_configured,
    )


@router.get("", response_model=list[PluginSummary])
async def list_plugins(
    _actor: RequireAdmin,
    plugin_manager: PluginManagerDep,
) -> list[PluginSummary]:
    """List all installed and discovered plugins with their operational status."""
    summaries: list[PluginSummary] = []
    for plugin in plugin_manager.list_plugins():
        plugin_path = plugin_manager._plugin_paths.get(plugin.manifest.id)
        is_builtin = True
        if plugin_path and plugin_manager.settings.user_plugins_dir:
            try:
                is_builtin = not plugin_path.is_relative_to(
                    plugin_manager.settings.user_plugins_dir
                )
            except (ValueError, OSError):
                is_builtin = True
        summaries.append(_build_summary(plugin, is_builtin=is_builtin))

    return sorted(summaries, key=lambda p: (p.category, p.name))


@router.get("/{plugin_id}", response_model=PluginDetail)
async def get_plugin(
    plugin_id: str,
    _actor: RequireAdmin,
    plugin_manager: PluginManagerDep,
) -> PluginDetail:
    """Get detailed manifest, capabilities, and masked configuration for a plugin."""
    plugin = plugin_manager.get_plugin(plugin_id)
    if plugin is None:
        raise NotFoundError(f"Plugin {plugin_id!r} not found", code="plugin_not_found")

    plugin_path = plugin_manager._plugin_paths.get(plugin_id)
    is_builtin = True
    if plugin_path and plugin_manager.settings.user_plugins_dir:
        try:
            is_builtin = not plugin_path.is_relative_to(plugin_manager.settings.user_plugins_dir)
        except (ValueError, OSError):
            is_builtin = True

    # Retrieve current persisted configuration and mask secrets
    persisted = await load_plugin_state(plugin_manager.sessionmaker, plugin_id)
    effective_config = dict(persisted.config or {})
    if plugin.config is not None and hasattr(plugin.config, "model_dump"):
        # Include applied defaults/fallbacks from plugin instance
        for k, v in plugin.config.model_dump().items():
            if k not in effective_config or not effective_config[k]:
                effective_config[k] = v

    masked_config = mask_plugin_config(effective_config, plugin.manifest.config_schema)

    return PluginDetail(
        id=plugin.manifest.id,
        name=plugin.manifest.name,
        version=plugin.manifest.version,
        description=plugin.manifest.description,
        author=plugin.manifest.author,
        category=plugin.manifest.category,
        min_hearth_version=plugin.manifest.min_hearth_version,
        status=plugin.status,
        capabilities=plugin.manifest.capabilities,
        error_message=plugin.error_message,
        is_builtin=is_builtin,
        config_schema=plugin.manifest.config_schema,
        config=masked_config,
    )


@router.get("/{plugin_id}/config", response_model=PluginConfigResponse)
async def get_plugin_config(
    plugin_id: str,
    _actor: RequireAdmin,
    plugin_manager: PluginManagerDep,
) -> PluginConfigResponse:
    """Get current configuration for a plugin with secrets masked."""
    plugin = plugin_manager.get_plugin(plugin_id)
    if plugin is None:
        raise NotFoundError(f"Plugin {plugin_id!r} not found", code="plugin_not_found")

    persisted = await load_plugin_state(plugin_manager.sessionmaker, plugin_id)
    effective_config = dict(persisted.config or {})
    if plugin.config is not None and hasattr(plugin.config, "model_dump"):
        for k, v in plugin.config.model_dump().items():
            if k not in effective_config or not effective_config[k]:
                effective_config[k] = v

    masked = mask_plugin_config(effective_config, plugin.manifest.config_schema)
    return PluginConfigResponse(
        plugin_id=plugin_id,
        config=masked,
        schema=plugin.manifest.config_schema,
    )


@router.put("/{plugin_id}/config", response_model=PluginConfigResponse)
async def update_plugin_config(
    plugin_id: str,
    body: UpdatePluginConfigRequest,
    actor: RequireAdmin,
    plugin_manager: PluginManagerDep,
    session: SessionDep,
) -> PluginConfigResponse:
    """Update configuration for a plugin, preserving unchanged masked secrets."""
    plugin = plugin_manager.get_plugin(plugin_id)
    if plugin is None:
        raise NotFoundError(f"Plugin {plugin_id!r} not found", code="plugin_not_found")

    # Load existing config to preserve secrets if masked values were submitted
    persisted = await load_plugin_state(plugin_manager.sessionmaker, plugin_id)
    existing = dict(persisted.config or {})
    if plugin.config is not None and hasattr(plugin.config, "model_dump"):
        for k, v in plugin.config.model_dump().items():
            if k not in existing or not existing[k]:
                existing[k] = v

    clean_config: dict[str, Any] = {}
    for key, value in body.config.items():
        if isinstance(value, str) and (value.startswith("…") or value == "****"):
            # Retain existing secret value
            if key in existing:
                clean_config[key] = existing[key]
        else:
            clean_config[key] = value

    updated_plugin = await plugin_manager.configure_plugin(plugin_id, clean_config)

    record_audit(
        session,
        actor_id=actor.id,
        action="plugin.configured",
        target_type="plugin",
        target_id=plugin_id,
        details={"keys": sorted(clean_config.keys())},
    )
    await session.commit()

    effective_config = dict(clean_config)
    masked = mask_plugin_config(effective_config, updated_plugin.manifest.config_schema)
    return PluginConfigResponse(
        plugin_id=plugin_id,
        config=masked,
        schema=updated_plugin.manifest.config_schema,
    )


@router.post("/{plugin_id}/enable", response_model=PluginSummary)
async def enable_plugin(
    plugin_id: str,
    actor: RequireAdmin,
    plugin_manager: PluginManagerDep,
    session: SessionDep,
) -> PluginSummary:
    """Enable a plugin."""
    try:
        plugin = await plugin_manager.enable_plugin(plugin_id)
    except KeyError:
        raise NotFoundError(f"Plugin {plugin_id!r} not found", code="plugin_not_found") from None

    record_audit(
        session,
        actor_id=actor.id,
        action="plugin.enabled",
        target_type="plugin",
        target_id=plugin_id,
    )
    await session.commit()

    return _build_summary(plugin)


@router.post("/{plugin_id}/disable", response_model=PluginSummary)
async def disable_plugin(
    plugin_id: str,
    actor: RequireAdmin,
    plugin_manager: PluginManagerDep,
    session: SessionDep,
) -> PluginSummary:
    """Disable a plugin."""
    try:
        plugin = await plugin_manager.disable_plugin(plugin_id)
    except KeyError:
        raise NotFoundError(f"Plugin {plugin_id!r} not found", code="plugin_not_found") from None

    record_audit(
        session,
        actor_id=actor.id,
        action="plugin.disabled",
        target_type="plugin",
        target_id=plugin_id,
    )
    await session.commit()

    return _build_summary(plugin)


@router.post("/reload", response_model=list[PluginSummary])
async def reload_plugins(
    actor: RequireAdmin,
    plugin_manager: PluginManagerDep,
    session: SessionDep,
) -> list[PluginSummary]:
    """Rescan plugin directories and reload active plugins."""
    plugins = await plugin_manager.discover_and_load()

    record_audit(
        session,
        actor_id=actor.id,
        action="plugin.reloaded",
        target_type="system",
        target_id="plugins",
        details={"count": len(plugins)},
    )
    await session.commit()

    return [_build_summary(p) for p in plugins]
