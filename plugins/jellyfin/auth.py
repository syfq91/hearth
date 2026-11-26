"""Jellyfin authentication header parsing, token minting, and user resolution."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, Query, Request
from jwt import InvalidTokenError

from app.core.config import Settings
from app.core.deps import KeysDep, SettingsDep
from app.core.errors import UnauthorizedError
from app.core.ids import uuid7
from app.core.secrets import Keys
from app.db.models import User
from app.db.session import SessionDep

JELLYFIN_TOKEN_AUDIENCE = "hearth-jellyfin"
JELLYFIN_TOKEN_TYPE = "jellyfin"
_ALGORITHM = "HS256"

_PARAM_RE = re.compile(r'([A-Za-z0-9_]+)\s*=\s*"([^"]*)"')


@dataclass(frozen=True)
class JellyfinAuthHeader:
    """Parsed Jellyfin/Emby MediaBrowser authorization header."""

    client: str = "Jellyfin Client"
    device: str = "Unknown Device"
    device_id: str = "unknown"
    version: str = "1.0.0"
    token: str | None = None


@dataclass(frozen=True)
class JellyfinTokenClaims:
    """Validated claims from a Jellyfin session token."""

    user_id: str
    token_version: int
    device_id: str
    client: str
    device: str
    expires_at: datetime


def server_id_for_keys(keys: Keys) -> str:
    """Return a stable 32-character hex server ID derived from the installation key."""
    return hashlib.sha256(b"hearth:jellyfin:server_id:" + keys.jwt).hexdigest()[:32]


def parse_jellyfin_auth_header(request: Request) -> JellyfinAuthHeader:
    """Extract client metadata and optional token from Jellyfin authorization headers."""
    raw = (
        request.headers.get("x-emby-authorization") or request.headers.get("authorization") or ""
    ).strip()

    params: dict[str, str] = {}
    if raw.lower().startswith(("mediabrowser ", "emby ")):
        for match in _PARAM_RE.finditer(raw):
            params[match.group(1).lower()] = match.group(2)

    token = (
        request.headers.get("x-emby-token")
        or request.headers.get("x-mediabrowser-token")
        or params.get("token")
        or request.query_params.get("api_key")
        or request.query_params.get("ApiKey")
    )

    if not token and raw.lower().startswith("bearer "):
        token = raw[7:].strip() or None

    return JellyfinAuthHeader(
        client=params.get("client") or "Jellyfin Client",
        device=params.get("device") or "Unknown Device",
        device_id=params.get("deviceid") or "unknown",
        version=params.get("version") or "1.0.0",
        token=token.strip() if token else None,
    )


def create_jellyfin_token(
    *,
    user_id: str,
    token_version: int,
    auth_header: JellyfinAuthHeader,
    settings: Settings,
    key: bytes,
    ttl_days: int = 30,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Mint a signed JWT scoped specifically to Jellyfin API endpoints."""
    issued_at = (now or datetime.now(UTC)).replace(microsecond=0)
    expires_at = issued_at + timedelta(days=max(1, ttl_days))
    payload = {
        "sub": user_id,
        "tv": token_version,
        "type": JELLYFIN_TOKEN_TYPE,
        "iss": settings.jwt_issuer,
        "aud": JELLYFIN_TOKEN_AUDIENCE,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid7(),
        "did": auth_header.device_id[:64],
        "cli": auth_header.client[:64],
        "dev": auth_header.device[:64],
    }
    token = jwt.encode(payload, key, algorithm=_ALGORITHM)
    return token, expires_at


def decode_jellyfin_token(token: str, *, settings: Settings, key: bytes) -> JellyfinTokenClaims:
    """Validate signature, issuer, audience, type, and expiry of a Jellyfin token."""
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=[_ALGORITHM],
            issuer=settings.jwt_issuer,
            audience=JELLYFIN_TOKEN_AUDIENCE,
            leeway=settings.jwt_leeway_seconds,
            options={"require": ["sub", "exp", "iat", "iss", "aud", "type"]},
        )
    except InvalidTokenError as exc:
        raise UnauthorizedError("Invalid or expired Jellyfin token", code="invalid_token") from exc

    if payload.get("type") != JELLYFIN_TOKEN_TYPE:
        raise UnauthorizedError("Invalid token type", code="invalid_token")

    try:
        return JellyfinTokenClaims(
            user_id=str(payload["sub"]),
            token_version=int(payload.get("tv", 0)),
            device_id=str(payload.get("did", "unknown")),
            client=str(payload.get("cli", "Jellyfin Client")),
            device=str(payload.get("dev", "Unknown Device")),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UnauthorizedError("Invalid token claims", code="invalid_token") from exc


async def get_jellyfin_user(
    request: Request,
    settings: SettingsDep,
    keys: KeysDep,
    session: SessionDep,
    api_key: Annotated[str | None, Query()] = None,
) -> User:
    """Authenticate a Jellyfin request and return the live, enabled Hearth user."""
    header = parse_jellyfin_auth_header(request)
    raw_token = header.token or api_key
    if not raw_token:
        raise UnauthorizedError("Authentication required", code="unauthorized")

    claims = decode_jellyfin_token(raw_token, settings=settings, key=keys.jwt)
    user = await session.get(User, claims.user_id)
    if user is None:
        raise UnauthorizedError("Invalid or expired token", code="invalid_token")
    if user.disabled:
        raise UnauthorizedError("Account is disabled", code="account_disabled")
    if user.token_version != claims.token_version:
        raise UnauthorizedError("Token has been revoked", code="token_revoked")
    return user


async def get_optional_jellyfin_user(
    request: Request,
    settings: SettingsDep,
    keys: KeysDep,
    session: SessionDep,
    api_key: Annotated[str | None, Query()] = None,
) -> User | None:
    """Resolve the Jellyfin user when a token is supplied, or return None."""
    header = parse_jellyfin_auth_header(request)
    raw_token = header.token or api_key
    if not raw_token:
        return None
    claims = decode_jellyfin_token(raw_token, settings=settings, key=keys.jwt)
    user = await session.get(User, claims.user_id)
    if user is None or user.disabled or user.token_version != claims.token_version:
        raise UnauthorizedError("Invalid or expired token", code="invalid_token")
    return user


JellyfinUser = Annotated[User, Depends(get_jellyfin_user)]
OptionalJellyfinUser = Annotated[User | None, Depends(get_optional_jellyfin_user)]
