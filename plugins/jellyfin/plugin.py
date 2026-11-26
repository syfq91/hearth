"""Jellyfin compatibility API adapter plugin."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.plugins.base import ApiAdapterPlugin, PluginContext
from app.core.plugins.manifest import PluginManifest
from plugins.jellyfin.router import create_jellyfin_router


class JellyfinConfig(BaseModel):
    """Runtime configuration for the Jellyfin compatibility plugin."""

    server_name: str = Field(
        default="Hearth",
        min_length=1,
        max_length=100,
        description="Display name advertised to Jellyfin clients",
    )
    advertised_version: str = Field(
        default="12.4.0",
        min_length=1,
        max_length=32,
        description="Jellyfin server version string advertised in /System/Info/Public",
    )
    token_ttl_days: int = Field(
        default=30,
        ge=1,
        le=365,
        description="How long Jellyfin client access tokens remain valid",
    )
    allow_transcoding: bool = Field(
        default=True,
        description="Allow Jellyfin clients to request HLS transcoded streams",
    )
    allow_unauthenticated_images: bool = Field(
        default=True,
        description="Allow artwork requests without an auth token",
    )
    expose_public_users: bool = Field(
        default=False,
        description="List user accounts on /Users/Public",
    )


class JellyfinPlugin(ApiAdapterPlugin[JellyfinConfig]):
    """Exposes Hearth libraries and media over a Jellyfin-compatible HTTP API."""

    def __init__(self, context: PluginContext, manifest: PluginManifest) -> None:
        super().__init__(context, manifest)
        self.config = JellyfinConfig()
        self._router: APIRouter | None = None

    def apply_configuration(self, config_data: dict[str, Any]) -> None:
        self.config = JellyfinConfig(**config_data)

    def get_router(self) -> APIRouter:
        if self._router is None:
            self._router = create_jellyfin_router()
        return self._router
