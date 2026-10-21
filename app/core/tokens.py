"""JWT access tokens and opaque refresh-token helpers."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from jwt import InvalidTokenError

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.ids import uuid7

ACCESS_TOKEN_TYPE = "access"
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
