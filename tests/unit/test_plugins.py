"""Unit tests for Hearth core plugin subsystem."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.plugins import (
    ManifestError,
    PluginManager,
    PluginStatus,
    load_all_plugins_state,
    load_manifest,
    load_plugin_state,
    mask_plugin_config,
    parse_manifest_data,
    save_plugin_state,
)

# -----------------------------------------------------------------------------
# Manifest tests
# -----------------------------------------------------------------------------


def test_manifest_validation_success() -> None:
    data = {
        "id": "org.hearth.plugins.test",
        "name": "Test Plugin",
        "version": "1.2.3",
        "entrypoint": "test_mod:TestClass",
        "capabilities": ["metadata.movie", "notification"],
        "min_hearth_version": "1.0.0",
    }
    manifest = parse_manifest_data(data)
    assert manifest.id == "org.hearth.plugins.test"
    assert manifest.name == "Test Plugin"
    assert manifest.version == "1.2.3"
    assert manifest.entrypoint == "test_mod:TestClass"
    assert manifest.capabilities == ["metadata.movie", "notification"]


def test_manifest_invalid_id() -> None:
    with pytest.raises(ManifestError, match="Plugin ID"):
        parse_manifest_data(
            {
                "id": "invalid id with spaces",
                "name": "T",
                "version": "1.0.0",
                "entrypoint": "m:C",
            }
        )


def test_manifest_invalid_version() -> None:
    with pytest.raises(ManifestError, match="valid semver"):
        parse_manifest_data(
            {
                "id": "valid.id",
                "name": "T",
                "version": "not-semver",
                "entrypoint": "m:C",
            }
        )


def test_manifest_invalid_entrypoint() -> None:
    with pytest.raises(ManifestError, match="module:ClassName"):
        parse_manifest_data(
            {
                "id": "valid.id",
                "name": "T",
                "version": "1.0.0",
                "entrypoint": "no_colon",
            }
        )


def test_load_manifest_file(tmp_path: Path) -> None:
    manifest_file = tmp_path / "plugin.json"
    manifest_file.write_text(
        json.dumps(
            {
                "id": "valid.test",
                "name": "File Test",
                "version": "0.1.0",
                "entrypoint": "mod:Cls",
            }
        ),
        encoding="utf-8",
    )
    manifest = load_manifest(manifest_file)
    assert manifest.id == "valid.test"


def test_load_manifest_file_missing(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(tmp_path / "nonexistent.json")


# -----------------------------------------------------------------------------
# Storage & Masking tests
# -----------------------------------------------------------------------------


def test_mask_plugin_config() -> None:
    config = {
        "api_key": "supersecretkey12345",
        "language": "en-US",
        "nested_token": "token9876",
        "normal_number": 42,
    }
    schema = {
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "secret": True},
            "language": {"type": "string"},
        },
    }
    masked = mask_plugin_config(config, schema)
    assert masked["api_key"] == "…2345"
    assert masked["language"] == "en-US"
    assert masked["nested_token"] == "…9876"
    assert masked["normal_number"] == 42


async def test_plugin_storage_roundtrip(db_sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    # 1. Default initial state
    initial = await load_plugin_state(db_sessionmaker, "test.plugin")
    assert initial.enabled is True
    assert initial.config == {}

    # 2. Save config and disable
    await save_plugin_state(
        db_sessionmaker,
        "test.plugin",
        enabled=False,
        config={"token": "secret123", "limit": 10},
    )

    loaded = await load_plugin_state(db_sessionmaker, "test.plugin")
    assert loaded.enabled is False
    assert loaded.config == {"token": "secret123", "limit": 10}

    # 3. Partial update config
    await save_plugin_state(
        db_sessionmaker,
        "test.plugin",
        config={"limit": 20},
    )
    updated = await load_plugin_state(db_sessionmaker, "test.plugin")
    assert updated.enabled is False
    assert updated.config == {"token": "secret123", "limit": 20}

    # 4. Load all plugins state
    all_states = await load_all_plugins_state(db_sessionmaker)
    assert "test.plugin" in all_states
    assert all_states["test.plugin"].config["limit"] == 20


# -----------------------------------------------------------------------------
# Manager & Dynamic Loading tests
# -----------------------------------------------------------------------------


class DummyPluginConfig(BaseModel):
    api_key: str = ""
    timeout: int = 10


def _create_sample_plugin(
    root: Path,
    plugin_id: str,
    *,
    code: str | None = None,
    entrypoint: str = "sample:SamplePlugin",
    capabilities: list[str] | None = None,
    config_schema: dict[str, Any] | None = None,
    min_hearth_version: str = "1.0.0",
) -> Path:
    p_dir = root / plugin_id.replace(".", "_")
    p_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "id": plugin_id,
        "name": f"Name for {plugin_id}",
        "version": "1.0.0",
        "entrypoint": entrypoint,
        "capabilities": capabilities or ["notification"],
        "min_hearth_version": min_hearth_version,
    }
    if config_schema:
        manifest["config_schema"] = config_schema

    (p_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")

    mod_name = entrypoint.split(":")[0]
    default_code = """
from app.core.plugins.base import BasePlugin
from pydantic import BaseModel

class SampleConfig(BaseModel):
    api_key: str = ""

class SamplePlugin(BasePlugin[SampleConfig]):
    initialized: bool = False
    disposed: bool = False

    async def initialize(self) -> None:
        self.initialized = True

    async def dispose(self) -> None:
        self.disposed = True

    def apply_configuration(self, config: dict) -> None:
        self.config = SampleConfig.model_validate(config)
"""
    (p_dir / f"{mod_name}.py").write_text(code or default_code, encoding="utf-8")
    return p_dir


async def test_plugin_manager_lifecycle(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    builtin_dir = tmp_path / "builtin"
    user_dir = tmp_path / "user"
    builtin_dir.mkdir()
    user_dir.mkdir()

    _create_sample_plugin(builtin_dir, "org.hearth.builtin_test", capabilities=["metadata.movie"])
    _create_sample_plugin(user_dir, "org.hearth.user_test", capabilities=["notification"])

    settings = Settings(
        plugins_dir=user_dir,
        builtin_plugins_dir=builtin_dir,
        data_dir=tmp_path / "data",
    )

    manager = PluginManager(settings, db_sessionmaker)
    plugins = await manager.discover_and_load()
    assert len(plugins) == 2

    builtin_plugin = manager.get_plugin("org.hearth.builtin_test")
    assert builtin_plugin is not None
    assert builtin_plugin.status == PluginStatus.ACTIVE
    assert getattr(builtin_plugin, "initialized", False) is True

    # Check capabilities filter
    movie_plugins = manager.get_plugins_with_capability("metadata.movie")
    assert len(movie_plugins) == 1
    assert movie_plugins[0].manifest.id == "org.hearth.builtin_test"

    # Disable plugin
    await manager.disable_plugin("org.hearth.builtin_test")
    assert builtin_plugin.status == PluginStatus.DISABLED
    assert getattr(builtin_plugin, "disposed", False) is True
    assert len(manager.get_plugins_with_capability("metadata.movie")) == 0

    # Re-enable plugin
    await manager.enable_plugin("org.hearth.builtin_test")
    assert builtin_plugin.status == PluginStatus.ACTIVE

    # Dispose all
    await manager.dispose_all()


async def test_plugin_manager_vendoring_support(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()

    p_dir = _create_sample_plugin(
        builtin_dir,
        "org.hearth.vendor_test",
        entrypoint="vendor_plugin:VendorPlugin",
        code="""
import custom_vendor_lib
from app.core.plugins.base import BasePlugin
from pydantic import BaseModel

class DummyConfig(BaseModel):
    pass

class VendorPlugin(BasePlugin[DummyConfig]):
    vendor_value: str = ""

    async def initialize(self) -> None:
        self.vendor_value = custom_vendor_lib.VALUE
""",
    )

    # Create vendor directory inside plugin
    vendor_dir = p_dir / "vendor"
    vendor_dir.mkdir()
    (vendor_dir / "custom_vendor_lib.py").write_text("VALUE = 'vendored_ok'", encoding="utf-8")

    settings = Settings(builtin_plugins_dir=builtin_dir, data_dir=tmp_path / "data")
    manager = PluginManager(settings, db_sessionmaker)
    await manager.discover_and_load()

    plugin = manager.get_plugin("org.hearth.vendor_test")
    assert plugin is not None
    assert plugin.status == PluginStatus.ACTIVE
    assert getattr(plugin, "vendor_value", "") == "vendored_ok"


async def test_plugin_manager_config_required(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()

    schema = {
        "type": "object",
        "properties": {"api_key": {"type": "string"}},
        "required": ["api_key"],
    }
    _create_sample_plugin(builtin_dir, "org.hearth.needs_config", config_schema=schema)

    settings = Settings(builtin_plugins_dir=builtin_dir, data_dir=tmp_path / "data")
    manager = PluginManager(settings, db_sessionmaker)
    await manager.discover_and_load()

    plugin = manager.get_plugin("org.hearth.needs_config")
    assert plugin is not None
    assert plugin.status == PluginStatus.CONFIG_REQUIRED

    # Now configure it
    await manager.configure_plugin("org.hearth.needs_config", {"api_key": "valid_key"})
    assert plugin.status == PluginStatus.ACTIVE
    assert getattr(plugin, "initialized", False) is True


async def test_plugin_manager_incompatible_version(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()

    _create_sample_plugin(
        builtin_dir,
        "org.hearth.future_plugin",
        min_hearth_version="99.0.0",
    )

    settings = Settings(builtin_plugins_dir=builtin_dir, data_dir=tmp_path / "data")
    manager = PluginManager(settings, db_sessionmaker)
    await manager.discover_and_load()

    plugin = manager.get_plugin("org.hearth.future_plugin")
    assert plugin is not None
    assert plugin.status == PluginStatus.FAILED
    assert "Incompatible" in (plugin.error_message or "")


async def test_plugin_manager_fault_isolation(
    tmp_path: Path,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    builtin_dir = tmp_path / "builtin"
    builtin_dir.mkdir()

    # 1. Plugin that raises in initialize()
    _create_sample_plugin(
        builtin_dir,
        "org.hearth.crashing_plugin",
        entrypoint="crash:CrashPlugin",
        code="""
from app.core.plugins.base import BasePlugin
from pydantic import BaseModel

class Dummy(BaseModel):
    pass

class CrashPlugin(BasePlugin[Dummy]):
    async def initialize(self) -> None:
        raise RuntimeError("Something exploded")
""",
    )

    # 2. Plugin with missing class
    _create_sample_plugin(
        builtin_dir,
        "org.hearth.missing_class",
        entrypoint="sample:NonExistentClass",
    )

    # 3. Healthy plugin alongside crashing ones
    _create_sample_plugin(builtin_dir, "org.hearth.healthy_plugin")

    settings = Settings(builtin_plugins_dir=builtin_dir, data_dir=tmp_path / "data")
    manager = PluginManager(settings, db_sessionmaker)

    # Should not raise exception
    await manager.discover_and_load()

    crashing = manager.get_plugin("org.hearth.crashing_plugin")
    assert crashing is not None
    assert crashing.status == PluginStatus.FAILED
    assert "Something exploded" in (crashing.error_message or "")

    missing = manager.get_plugin("org.hearth.missing_class")
    assert missing is not None
    assert missing.status == PluginStatus.FAILED
    assert "not found" in (missing.error_message or "")

    healthy = manager.get_plugin("org.hearth.healthy_plugin")
    assert healthy is not None
    assert healthy.status == PluginStatus.ACTIVE
