"""Unit tests for access-token signing and refresh-token hashing."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.tokens import (
    create_access_token,
    create_media_token,
    decode_access_token,
    generate_refresh_token,
    hash_refresh_token,
    refresh_expiry,
    verify_media_token,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(env="testing", log_level="WARNING", secret_key="k" * 32)


def _token(settings: Settings, **overrides: object) -> tuple[str, datetime]:
    values: dict[str, object] = {
        "user_id": "user-1",
        "role": "admin",
        "token_version": 3,
        "settings": settings,
        "key": b"key" * 16,
    }
    values.update(overrides)
    return create_access_token(**values)  # type: ignore[arg-type]


def test_access_token_carries_expected_claims_and_ttl(settings: Settings) -> None:
    token, expires_at = _token(settings)
    claims = decode_access_token(token, settings=settings, key=b"key" * 16)

    assert claims.user_id == "user-1"
    assert claims.role == "admin"
    assert claims.token_version == 3
    assert claims.expires_at == expires_at
    payload = jwt.decode(
        token,
        b"key" * 16,
        algorithms=["HS256"],
        issuer="hearth",
        audience="hearth-api",
    )
    assert payload["exp"] - payload["iat"] == 15 * 60
    assert payload["jti"]


def test_access_token_expires_after_configured_minutes(settings: Settings) -> None:
    issued = datetime.now(UTC) - timedelta(minutes=16)
    token, _ = _token(settings, now=issued)

    with pytest.raises(UnauthorizedError) as excinfo:
        decode_access_token(token, settings=settings, key=b"key" * 16)

    assert excinfo.value.code == "invalid_token"


def test_access_token_with_wrong_key_is_rejected(settings: Settings) -> None:
    token, _ = _token(settings)

    with pytest.raises(UnauthorizedError):
        decode_access_token(token, settings=settings, key=b"other" * 16)


def test_access_token_with_wrong_audience_or_type_is_rejected(settings: Settings) -> None:
    token, _ = _token(settings)
    payload = jwt.decode(
        token, b"key" * 16, algorithms=["HS256"], issuer="hearth", audience="hearth-api"
    )

    wrong_audience = jwt.encode({**payload, "aud": "other"}, b"key" * 16, algorithm="HS256")
    wrong_type = jwt.encode({**payload, "type": "media"}, b"key" * 16, algorithm="HS256")

    with pytest.raises(UnauthorizedError):
        decode_access_token(wrong_audience, settings=settings, key=b"key" * 16)
    with pytest.raises(UnauthorizedError):
        decode_access_token(wrong_type, settings=settings, key=b"key" * 16)


def test_refresh_tokens_are_random_and_hashed() -> None:
    first = generate_refresh_token()
    second = generate_refresh_token()

    assert first != second
    assert len(first) >= 60
    digest = hash_refresh_token(first)
    assert len(digest) == 64
    assert digest != first
    assert hash_refresh_token(first) == digest
    assert hash_refresh_token(second) != digest


def test_refresh_expiry_uses_configured_days(settings: Settings) -> None:
    now = datetime.now(UTC)

    assert refresh_expiry(settings, now) == now + timedelta(days=30)


def test_media_token_round_trip_scopes_user_and_item(settings: Settings) -> None:
    token, expires_at = create_media_token(
        user_id="user-1",
        token_version=2,
        settings=settings,
        key=b"media" * 8,
        item_id="item-9",
    )

    claims = verify_media_token(token, key=b"media" * 8)

    assert claims.user_id == "user-1"
    assert claims.item_id == "item-9"
    assert claims.token_version == 2
    assert claims.expires_at == expires_at
    assert "." in token


def test_media_token_is_tamper_evident(settings: Settings) -> None:
    token, _ = create_media_token(
        user_id="user-1", token_version=0, settings=settings, key=b"media" * 8
    )
    encoded, signature = token.split(".")
    forged = f"{encoded}.{'A' * len(signature)}"

    with pytest.raises(UnauthorizedError):
        verify_media_token(forged, key=b"media" * 8)
    with pytest.raises(UnauthorizedError):
        verify_media_token(encoded, key=b"media" * 8)
    with pytest.raises(UnauthorizedError):
        verify_media_token("not-a-token", key=b"media" * 8)


def test_media_token_expires(settings: Settings) -> None:
    token, _ = create_media_token(
        user_id="user-1",
        token_version=0,
        settings=settings,
        key=b"media" * 8,
        now=datetime.now(UTC) - timedelta(hours=7),
    )

    with pytest.raises(UnauthorizedError) as excinfo:
        verify_media_token(token, key=b"media" * 8)

    assert excinfo.value.code == "invalid_media_token"


def test_media_token_wrong_key_is_rejected(settings: Settings) -> None:
    token, _ = create_media_token(
        user_id="user-1", token_version=0, settings=settings, key=b"media" * 8
    )

    with pytest.raises(UnauthorizedError):
        verify_media_token(token, key=b"other!" * 8)
