"""Plugin manifest schema, validation, and loading."""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PluginCapability(StrEnum):
    """Known plugin capability identifiers."""

    METADATA_MOVIE = "metadata.movie"
    METADATA_SERIES = "metadata.series"
    METADATA_MUSIC = "metadata.music"
    METADATA_BOOK = "metadata.book"
    NOTIFICATION = "notification"
    API_ROUTER = "api.router"
    SCROBBLER = "scrobbler"


class ManifestError(Exception):
    """Raised when a plugin manifest cannot be read or validated."""


_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{3,64}$")
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[a-zA-Z0-9_.]+)?$")


class PluginManifest(BaseModel):
    """Typed representation of a plugin.json descriptor."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(description="Unique plugin identifier, e.g. org.hearth.plugins.tmdb")
    name: str = Field(min_length=1, max_length=100, description="Display name")
    version: str = Field(description="Semver version string, e.g. 1.0.0")
    description: str = Field(default="", description="Summary of plugin features")
    author: str = Field(default="Unknown", description="Plugin author or organization")
    category: str = Field(default="General", description="Category for sorting/filtering")
    min_hearth_version: str = Field(default="1.0.0", description="Minimum Hearth version required")
    entrypoint: str = Field(
        description="Path to entrypoint class in module:ClassName format, e.g. plugin:TMDBPlugin"
    )
    capabilities: list[str] = Field(
        default_factory=list, description="List of declared capabilities"
    )
    config_schema: dict[str, Any] | None = Field(
        default=None, description="JSON Schema for user-configurable plugin settings"
    )
    homepage: str | None = Field(default=None, description="Project website or repository URL")
    license: str | None = Field(default=None, description="Software license")

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        if not _ID_PATTERN.match(v):
            raise ValueError(f"Plugin ID {v!r} must be 3-64 characters matching [a-zA-Z0-9_.-]")
        return v

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        if not _SEMVER_PATTERN.match(v):
            raise ValueError(f"Plugin version {v!r} must be a valid semver string (e.g. 1.0.0)")
        return v

    @field_validator("entrypoint")
    @classmethod
    def _validate_entrypoint(cls, v: str) -> str:
        if ":" not in v:
            raise ValueError(
                f"Entrypoint {v!r} must be in 'module:ClassName' format (e.g. 'plugin:MyPlugin')"
            )
        module_part, class_part = v.split(":", 1)
        if not module_part.strip() or not class_part.strip():
            raise ValueError(f"Entrypoint {v!r} must contain both a module name and a class name")
        return v


def parse_manifest_data(data: dict[str, Any], source_path: Path | None = None) -> PluginManifest:
    """Parse and validate raw dictionary data into a PluginManifest."""
    try:
        return PluginManifest.model_validate(data)
    except Exception as exc:
        location = f" in {source_path}" if source_path else ""
        raise ManifestError(f"Invalid plugin manifest{location}: {exc}") from exc


def load_manifest(manifest_path: Path) -> PluginManifest:
    """Load, parse, and validate a plugin.json file from disk."""
    if not manifest_path.is_file():
        raise ManifestError(f"Plugin manifest file not found: {manifest_path}")

    try:
        content = manifest_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"Invalid JSON in manifest {manifest_path}: {exc}") from exc
    except OSError as exc:
        raise ManifestError(f"Could not read manifest {manifest_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ManifestError(f"Manifest root must be a JSON object in {manifest_path}")

    return parse_manifest_data(data, source_path=manifest_path)
