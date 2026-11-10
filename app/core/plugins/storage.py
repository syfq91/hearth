"""Database persistence and secret masking for plugin states and configurations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Setting

PLUGIN_SETTING_PREFIX = "plugin."


@dataclass(frozen=True)
class PersistedPluginState:
    """Stored state of a plugin in the database."""

    enabled: bool = True
    config: dict[str, Any] | None = None


def get_plugin_setting_key(plugin_id: str) -> str:
    """Return the database setting key for a plugin."""
    return f"{PLUGIN_SETTING_PREFIX}{plugin_id}"


def mask_secret(value: str | None) -> str | None:
    """Return a display hint that reveals at most the last four characters."""
    if not value:
        return None
    if len(value) <= 4:
        return "****"
    return f"…{value[-4:]}"


def mask_plugin_config(
    config: dict[str, Any],
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy of config where secret fields are masked."""
    masked: dict[str, Any] = {}
    properties = schema.get("properties", {}) if schema and isinstance(schema, dict) else {}

    secret_keys = set()
    for prop_name, prop_def in properties.items():
        if isinstance(prop_def, dict) and prop_def.get("secret") is True:
            secret_keys.add(prop_name)

    # Fallback to key name heuristics if schema did not explicitly flag
    for k, v in config.items():
        is_secret = k in secret_keys or any(
            hint in k.lower() for hint in ("key", "token", "secret", "password")
        )
        if is_secret and isinstance(v, str):
            masked[k] = mask_secret(v)
        elif is_secret and v is not None:
            masked[k] = "****"
        else:
            masked[k] = v

    return masked


async def load_plugin_state(
    sessionmaker: async_sessionmaker[AsyncSession],
    plugin_id: str,
) -> PersistedPluginState:
    """Load the persisted state of a single plugin."""
    key = get_plugin_setting_key(plugin_id)
    async with sessionmaker() as session:
        row = await session.get(Setting, key)
        if row is None:
            return PersistedPluginState(enabled=True, config={})

        try:
            data = json.loads(row.value)
        except (TypeError, ValueError):
            return PersistedPluginState(enabled=True, config={})

        if not isinstance(data, dict):
            return PersistedPluginState(enabled=True, config={})

        enabled = data.get("enabled", True)
        config = data.get("config", {})
        return PersistedPluginState(
            enabled=bool(enabled),
            config=config if isinstance(config, dict) else {},
        )


async def load_all_plugins_state(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> dict[str, PersistedPluginState]:
    """Load persisted states for all plugins matching the plugin prefix."""
    prefix = PLUGIN_SETTING_PREFIX
    async with sessionmaker() as session:
        query = select(Setting).where(Setting.key.like(f"{prefix}%"))
        rows = await session.scalars(query)

        states: dict[str, PersistedPluginState] = {}
        for row in rows:
            plugin_id = row.key[len(prefix) :]
            try:
                data = json.loads(row.value)
            except (TypeError, ValueError):
                continue

            if isinstance(data, dict):
                enabled = data.get("enabled", True)
                config = data.get("config", {})
                states[plugin_id] = PersistedPluginState(
                    enabled=bool(enabled),
                    config=config if isinstance(config, dict) else {},
                )

        return states


async def save_plugin_state(
    sessionmaker: async_sessionmaker[AsyncSession],
    plugin_id: str,
    *,
    enabled: bool | None = None,
    config: dict[str, Any] | None = None,
) -> PersistedPluginState:
    """Save or update the enabled state and configuration for a plugin."""
    key = get_plugin_setting_key(plugin_id)
    async with sessionmaker() as session:
        row = await session.get(Setting, key)
        current_data: dict[str, Any] = {}
        if row is not None:
            try:
                parsed = json.loads(row.value)
                if isinstance(parsed, dict):
                    current_data = parsed
            except (TypeError, ValueError):
                pass

        if enabled is not None:
            current_data["enabled"] = enabled
        elif "enabled" not in current_data:
            current_data["enabled"] = True

        if config is not None:
            # Merge with existing configuration to allow partial updates
            existing_config = current_data.get("config", {})
            if isinstance(existing_config, dict):
                # When an empty string or None is sent for a field, clear it
                merged = dict(existing_config)
                merged.update(config)
                current_data["config"] = merged
            else:
                current_data["config"] = config
        elif "config" not in current_data:
            current_data["config"] = {}

        encoded = json.dumps(current_data)
        if row is None:
            session.add(Setting(key=key, value=encoded))
        else:
            row.value = encoded

        await session.commit()

        return PersistedPluginState(
            enabled=bool(current_data["enabled"]),
            config=current_data.get("config", {}),
        )
