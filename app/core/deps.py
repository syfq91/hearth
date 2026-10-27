"""Shared FastAPI dependencies."""

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.ratelimit import SlidingWindowLimiter
from app.core.secrets import Keys, load_keys
from app.core.tokens import MediaTokenClaims, decode_access_token, verify_media_token
from app.db.models import Library, LibraryAccess, User
from app.db.session import SessionDep

_bearer = HTTPBearer(auto_error=False, description="Hearth access token")


def get_app_settings(request: Request) -> Settings:
    """Return the settings instance bound to the running application."""
    return cast(Settings, request.app.state.settings)


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_login_limiter(request: Request) -> SlidingWindowLimiter:
    """Return the process-wide login attempt limiter."""
    return cast(SlidingWindowLimiter, request.app.state.login_limiter)


LoginLimiterDep = Annotated[SlidingWindowLimiter, Depends(get_login_limiter)]


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    """Return the application's session factory for streaming endpoints."""
    return cast(async_sessionmaker[AsyncSession], request.app.state.sessionmaker)


SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]


def client_ip(request: Request, settings: Settings) -> str:
    """Return the caller IP, honoring proxy headers only when configured.

    ``X-Forwarded-For`` is trivially spoofable, so it is ignored by default.
    Deployments behind a reverse proxy can set ``HEARTH_TRUST_PROXY_HEADERS``.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first[:64]
    return request.client.host if request.client else "unknown"


def get_keys(request: Request, settings: SettingsDep) -> Keys:
    """Return the installation key set, loading it lazily if needed."""
    keys = getattr(request.app.state, "keys", None)
    if keys is None:
        keys = load_keys(settings)
        request.app.state.keys = keys
    return cast(Keys, keys)


KeysDep = Annotated[Keys, Depends(get_keys)]

BearerCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


async def get_current_user(
    credentials: BearerCredentials,
    settings: SettingsDep,
    keys: KeysDep,
    session: SessionDep,
) -> User:
    """Resolve and validate the bearer token into a live, enabled user.

    The database is consulted on every request so disabling an account or
    bumping its token version invalidates outstanding access tokens
    immediately, despite JWTs being stateless.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError()

    claims = decode_access_token(credentials.credentials, settings=settings, key=keys.jwt)
    user = await session.get(User, claims.user_id)
    if user is None:
        raise UnauthorizedError("Invalid or expired token", code="invalid_token")
    if user.disabled:
        raise UnauthorizedError("Account is disabled", code="account_disabled")
    if user.token_version != claims.token_version:
        raise UnauthorizedError("Token has been revoked", code="token_revoked")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_events_user(
    credentials: BearerCredentials,
    settings: SettingsDep,
    keys: KeysDep,
    session: SessionDep,
    token: Annotated[
        str | None,
        Query(description="Access token query parameter for EventSource clients"),
    ] = None,
) -> User:
    """Resolve and validate the bearer token from Authorization header or ?token query parameter."""
    raw_token: str | None = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        raw_token = credentials.credentials
    elif token:
        raw_token = token

    if not raw_token:
        raise UnauthorizedError()

    claims = decode_access_token(raw_token, settings=settings, key=keys.jwt)
    user = await session.get(User, claims.user_id)
    if user is None:
        raise UnauthorizedError("Invalid or expired token", code="invalid_token")
    if user.disabled:
        raise UnauthorizedError("Account is disabled", code="account_disabled")
    if user.token_version != claims.token_version:
        raise UnauthorizedError("Token has been revoked", code="token_revoked")
    return user


EventsUser = Annotated[User, Depends(get_events_user)]


async def get_media_token_claims(
    token: Annotated[str, Query(description="Media access token")],
    keys: KeysDep,
) -> MediaTokenClaims:
    """Validate a media token supplied in the query string."""
    return verify_media_token(token, key=keys.media)


MediaTokenClaimsDep = Annotated[MediaTokenClaims, Depends(get_media_token_claims)]


async def get_media_user(
    claims: MediaTokenClaimsDep,
    session: SessionDep,
) -> User:
    """Resolve a media token to a live, enabled user.

    Media tokens embed a token version so a password reset or forced logout
    invalidates URLs that are already in a player's playlist.
    """
    return await _load_media_user(claims, session)


async def _load_media_user(claims: MediaTokenClaims, session: AsyncSession) -> User:
    user = await session.get(User, claims.user_id)
    if user is None:
        raise UnauthorizedError("Invalid media token", code="invalid_media_token")
    if user.disabled:
        raise UnauthorizedError("Account is disabled", code="account_disabled")
    if user.token_version != claims.token_version:
        raise UnauthorizedError("Media token has been revoked", code="token_revoked")
    return user


MediaUser = Annotated[User, Depends(get_media_user)]


@dataclass(frozen=True)
class Viewer:
    """A request identity resolved from an access token or a media token."""

    user: User
    item_id: str | None = None


async def get_viewer(
    credentials: BearerCredentials,
    settings: SettingsDep,
    keys: KeysDep,
    session: SessionDep,
    token: Annotated[str | None, Query(description="Media access token")] = None,
) -> Viewer:
    """Resolve a normal bearer token, or a media token from the query string.

    Media elements (``<img>``, later ``<video>``) cannot attach an
    ``Authorization`` header, so this dependency lets the same read endpoints
    accept either. A media token may be scoped to one item.
    """
    if token is not None:
        claims = verify_media_token(token, key=keys.media)
        return Viewer(user=await _load_media_user(claims, session), item_id=claims.item_id)
    return Viewer(user=await get_current_user(credentials, settings, keys, session))


ViewerDep = Annotated[Viewer, Depends(get_viewer)]


async def require_user(user: CurrentUser) -> User:
    """Require any authenticated, enabled account."""
    return user


RequireUser = Annotated[User, Depends(require_user)]


async def require_admin(user: CurrentUser) -> User:
    """Require an administrator account."""
    if user.role != "admin":
        raise ForbiddenError("Administrator access required", code="admin_required")
    return user


RequireAdmin = Annotated[User, Depends(require_admin)]


async def user_can_access_library(session: AsyncSession, user: User, library_id: str) -> bool:
    """Return whether ``user`` may see the given library."""
    if user.role == "admin":
        return True
    granted = await session.scalar(
        select(LibraryAccess.id).where(
            LibraryAccess.library_id == library_id,
            LibraryAccess.user_id == user.id,
        )
    )
    return granted is not None


async def require_library_access(
    library_id: str,
    user: CurrentUser,
    session: SessionDep,
) -> Library:
    """Resolve a library the current user is allowed to see.

    Missing libraries and libraries the user cannot access both raise 404 so
    the API does not reveal the existence of other users' libraries. Admins
    may access every library.
    """
    library = await session.get(Library, library_id)
    if library is None:
        raise NotFoundError("Library not found", code="library_not_found")

    if not await user_can_access_library(session, user, library.id):
        raise NotFoundError("Library not found", code="library_not_found")
    return library


LibraryAccessDep = Annotated[Library, Depends(require_library_access)]
