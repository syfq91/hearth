"""Plugin base classes, status enums, and extension interfaces."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.plugins.manifest import PluginManifest
from app.services.metadata.base import MetadataProvider

if TYPE_CHECKING:
    from fastapi import APIRouter


class PluginStatus(StrEnum):
    """Lifecycle and operational status of a plugin."""

    ACTIVE = "active"
    DISABLED = "disabled"
    FAILED = "failed"
    CONFIG_REQUIRED = "config_required"


@dataclass(frozen=True)
class PluginContext:
    """Runtime dependencies and storage provided to a plugin instance."""

    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    logger: logging.Logger
    data_dir: Path
    plugin_dir: Path


class BasePlugin[TConfig: BaseModel](ABC):
    """Base class that all Hearth plugins inherit from."""

    manifest: PluginManifest
    context: PluginContext
    status: PluginStatus = PluginStatus.ACTIVE
    error_message: str | None = None
    config: TConfig | None = None

    def __init__(self, context: PluginContext, manifest: PluginManifest) -> None:
        self.context = context
        self.manifest = manifest
        self.status = PluginStatus.ACTIVE
        self.error_message = None
        self.config = None

    async def initialize(self) -> None:
        """Asynchronous setup lifecycle hook called once when plugin is loaded."""
        return None

    async def dispose(self) -> None:
        """Asynchronous teardown hook called when plugin is unloaded or disabled."""
        return None

    def apply_configuration(self, config_data: dict[str, Any]) -> None:
        """Apply new configuration values to the plugin.

        Subclasses may override this to parse config_data into their typed
        configuration model.
        """
        return None


class MetadataPlugin[TConfig: BaseModel](BasePlugin[TConfig], MetadataProvider, ABC):
    """A plugin that provides metadata fetching and candidate searching."""

    @property
    @abstractmethod
    def supported_media_types(self) -> tuple[str, ...]:
        """Media item types this provider supports (e.g. 'movie', 'series', 'book', 'music')."""


@dataclass(frozen=True)
class ServerEvent:
    """Asynchronous server event broadcast to subscribed plugins."""

    event_type: str
    payload: dict[str, Any]
    timestamp: datetime


class NotificationPlugin[TConfig: BaseModel](BasePlugin[TConfig], ABC):
    """A plugin that receives server events and dispatches notifications."""

    @abstractmethod
    async def on_event(self, event: ServerEvent) -> None:
        """Handle an incoming server lifecycle or media event."""


class ApiAdapterPlugin[TConfig: BaseModel](BasePlugin[TConfig], ABC):
    """A plugin that exposes custom HTTP endpoints via a FastAPI APIRouter."""

    @abstractmethod
    def get_router(self) -> APIRouter:
        """Return the APIRouter to mount onto the application."""


class ScrobblerPlugin[TConfig: BaseModel](BasePlugin[TConfig], ABC):
    """A plugin that reports playback activity to external scrobbling services."""

    @abstractmethod
    async def now_playing(self, user_id: str, item_id: str, position_seconds: float) -> None:
        """Report currently playing item and progress."""

    @abstractmethod
    async def scrobble(self, user_id: str, item_id: str, completed_at: datetime) -> None:
        """Report completed playback."""
