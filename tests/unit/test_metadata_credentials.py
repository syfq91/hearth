"""Tests for persisted online-metadata provider credentials."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services.metadata.credentials import (
    PROVIDER_CREDENTIALS,
    apply_credential_overrides,
    load_credential_overrides,
    mask_credential,
    save_credential_overrides,
)


async def test_overrides_take_precedence_and_can_be_cleared(
    db_settings: Settings,
    db_session: AsyncSession,
) -> None:
    db_settings.tmdb_api_key = "env-key"

    await save_credential_overrides(db_session, {"tmdb_api_key": "db-key"})
    await db_session.commit()

    overrides = await load_credential_overrides(db_session)
    assert overrides == {"tmdb_api_key": "db-key"}

    await apply_credential_overrides(db_settings, db_session)
    assert db_settings.tmdb_api_key == "db-key"

    # A stored null is a deliberate clear and must not fall back to the env.
    await save_credential_overrides(db_session, {"tmdb_api_key": None})
    await db_session.commit()

    await apply_credential_overrides(db_settings, db_session)
    assert db_settings.tmdb_api_key is None


def test_mask_credential_reveals_at_most_last_four_characters() -> None:
    assert mask_credential(None) is None
    assert mask_credential("") is None
    assert mask_credential("abcd") == "••••"
    assert mask_credential("abcdef0123456789") == "••••6789"


def test_every_credential_field_is_a_settings_field(db_settings: Settings) -> None:
    for credential in PROVIDER_CREDENTIALS:
        if credential.settings_attr is not None:
            assert hasattr(db_settings, credential.settings_attr)
        if credential.enabled_attr is not None:
            assert hasattr(db_settings, credential.enabled_attr)
