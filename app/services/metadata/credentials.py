"""Store administrator-supplied API keys for online metadata providers.

Provider credentials can come from the environment (``HEARTH_TMDB_API_KEY`` and
friends) or from the admin UI, which writes overrides to the ``settings`` table.
Database overrides win over the environment and are copied onto the shared
``Settings`` object, so the API process and the background worker agree on the
same values without a restart.

The keys are never returned by the API; callers only expose a masked hint and
whether a value is configured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Setting

SETTINGS_PREFIX = "metadata."


@dataclass(frozen=True)
class ProviderCredential:
    """One online metadata provider and the settings field holding its key."""

    name: str
    label: str
    description: str
    requires_api_key: bool
    settings_attr: str | None = None
    enabled_attr: str | None = None
    docs_url: str | None = None

    @property
    def setting_key(self) -> str | None:
        if self.settings_attr is None:
            return None
        return f"{SETTINGS_PREFIX}{self.settings_attr}"


PROVIDER_CREDENTIALS: tuple[ProviderCredential, ...] = (
    ProviderCredential(
        name="tmdb",
        label="TMDB",
        description=(
            "Movies and TV series metadata, artwork and cast. A v3 API key or a "
            "v4 read access token works."
        ),
        requires_api_key=True,
        settings_attr="tmdb_api_key",
        enabled_attr="tmdb_api_key",
        docs_url="https://www.themoviedb.org/settings/api",
    ),
    ProviderCredential(
        name="musicbrainz",
        label="MusicBrainz",
        description="Artist and album metadata. No API key is required.",
        requires_api_key=False,
        enabled_attr="musicbrainz_enabled",
        docs_url="https://musicbrainz.org/doc/MusicBrainz_API",
    ),
    ProviderCredential(
        name="openlibrary",
        label="Open Library",
        description="Primary book metadata provider. No API key is required.",
        requires_api_key=False,
        enabled_attr="openlibrary_enabled",
        docs_url="https://openlibrary.org/developers/api",
    ),
    ProviderCredential(
        name="googlebooks",
        label="Google Books",
        description=("Book metadata fallback. An API key raises the anonymous request quota."),
        requires_api_key=True,
        settings_attr="google_books_api_key",
        enabled_attr="google_books_enabled",
        docs_url="https://developers.google.com/books/docs/v1/using#APIKey",
    ),
)


async def load_credential_overrides(session: AsyncSession) -> dict[str, str | None]:
    """Return stored overrides keyed by settings attribute.

    A ``None`` value is a deliberate clear, distinct from a missing row (which
    means the environment value still applies).
    """
    rows = await session.scalars(select(Setting).where(Setting.key.like(f"{SETTINGS_PREFIX}%")))
    overrides: dict[str, str | None] = {}
    for row in rows:
        try:
            value = json.loads(row.value)
        except (TypeError, ValueError):
            value = None
        if value is not None and not isinstance(value, str):
            continue
        overrides[row.key[len(SETTINGS_PREFIX) :]] = value
    return overrides


async def save_credential_overrides(
    session: AsyncSession,
    changes: dict[str, str | None],
) -> None:
    """Upsert credential overrides; values are JSON-encoded for forward safety."""
    for attr, value in changes.items():
        key = f"{SETTINGS_PREFIX}{attr}"
        row = await session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=json.dumps(value)))
        else:
            row.value = json.dumps(value)


async def apply_credential_overrides(settings: Settings, session: AsyncSession) -> None:
    """Copy stored overrides onto ``settings`` in place."""
    overrides = await load_credential_overrides(session)
    for attr, value in overrides.items():
        if hasattr(settings, attr):
            setattr(settings, attr, value)


def resolve_credential(
    settings: Settings,
    overrides: dict[str, str | None],
    credential: ProviderCredential,
) -> tuple[str | None, str]:
    """Return ``(value, source)`` for a provider's key.

    ``source`` is ``database`` when an override exists (even a cleared one),
    ``environment`` when only the environment provided a value, else ``none``.
    """
    if credential.settings_attr is None:
        return None, "none"
    if credential.settings_attr in overrides:
        return overrides[credential.settings_attr], "database"
    value = getattr(settings, credential.settings_attr, None)
    return value, "environment" if value else "none"


def mask_credential(value: str | None) -> str | None:
    """Return a display hint that reveals at most the last four characters."""
    if not value:
        return None
    if len(value) <= 4:
        return "••••"
    return f"••••{value[-4:]}"
