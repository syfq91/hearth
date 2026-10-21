"""JWT access tokens, media tokens and opaque refresh-token helpers."""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from jwt import InvalidTokenError

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.ids import uuid7

ACCESS_TOKEN_TYPE = "access"
MEDIA_TOKEN_SCOPE = "media"
_ALGORITHM = "HS256"


@dataclass(frozen=True)
class AccessTokenClaims:
    """Validated claims from an access token."""

    user_id: str
    role: str
    token_version: int
    expires_at: datetime


def create_access_token(
    *,
    user_id: str,
    role: str,
    token_version: int,
    settings: Settings,
    key: bytes,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Create a short-lived signed access token and return it with its expiry."""
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    expires_at = issued_at + timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": user_id,
        "role": role,
        "tv": token_version,
        "type": ACCESS_TOKEN_TYPE,
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid7(),
    }
    token = jwt.encode(payload, key, algorithm=_ALGORITHM)
    return token, expires_at


def decode_access_token(token: str, *, settings: Settings, key: bytes) -> AccessTokenClaims:
    """Validate signature, issuer, type and expiry of an access token."""
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=[_ALGORITHM],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "exp", "iat", "iss", "aud", "type"]},
        )
    except InvalidTokenError as exc:
        raise UnauthorizedError("Invalid or expired token", code="invalid_token") from exc

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        raise UnauthorizedError("Invalid token type", code="invalid_token")

    try:
        return AccessTokenClaims(
            user_id=str(payload["sub"]),
            role=str(payload.get("role", "user")),
            token_version=int(payload.get("tv", 0)),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UnauthorizedError("Invalid token claims", code="invalid_token") from exc


def generate_refresh_token() -> str:
    """Return a high-entropy opaque refresh token."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """Return the SHA-256 hex digest stored in the database."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def refresh_expiry(settings: Settings, now: datetime | None = None) -> datetime:
    """Return the expiry timestamp for a newly issued refresh token."""
    return (now or datetime.now(UTC)) + timedelta(days=settings.refresh_token_days)


@dataclass(frozen=True)
class MediaTokenClaims:
    """Validated claims from a media access token."""

    user_id: str
    item_id: str | None
    token_version: int
    expires_at: datetime


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def create_media_token(
    *,
    user_id: str,
    token_version: int,
    settings: Settings,
    key: bytes,
    item_id: str | None = None,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Mint an HMAC-signed token for query-string media consumers.

    ``<video>``, ``<img>`` and ``<audio>`` elements cannot attach an
    Authorization header, so these tokens travel in URLs. They are scoped to a
    user, optionally to one item, and expire after ``media_token_seconds``.
    """
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    expires_at = issued_at + timedelta(seconds=settings.media_token_seconds)
    payload: dict[str, Any] = {
        "sub": user_id,
        "item": item_id,
        "tv": token_version,
        "scope": MEDIA_TOKEN_SCOPE,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid7(),
    }
    encoded = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = _b64url_encode(hmac.new(key, encoded.encode("ascii"), hashlib.sha256).digest())
    return f"{encoded}.{signature}", expires_at


def verify_media_token(
    token: str,
    *,
    key: bytes,
    now: datetime | None = None,
) -> MediaTokenClaims:
    """Verify a media token's signature, scope and expiry."""
    try:
        encoded, signature = token.split(".", 1)
        expected = _b64url_encode(hmac.new(key, encoded.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise UnauthorizedError("Invalid media token", code="invalid_media_token")
        payload = json.loads(_b64url_decode(encoded))
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise UnauthorizedError("Invalid media token", code="invalid_media_token") from exc

    if not isinstance(payload, dict) or payload.get("scope") != MEDIA_TOKEN_SCOPE:
        raise UnauthorizedError("Invalid media token", code="invalid_media_token")

    try:
        expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=UTC)
        user_id = str(payload["sub"])
        token_version = int(payload.get("tv", 0))
    except (KeyError, TypeError, ValueError) as exc:
        raise UnauthorizedError("Invalid media token", code="invalid_media_token") from exc

    if expires_at <= (now or datetime.now(UTC)):
        raise UnauthorizedError("Media token expired", code="invalid_media_token")

    item = payload.get("item")
    return MediaTokenClaims(
        user_id=user_id,
        item_id=str(item) if item is not None else None,
        token_version=token_version,
        expires_at=expires_at,
    )
