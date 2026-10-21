"""Shared FastAPI dependencies."""

from typing import Annotated, cast

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.core.config import Settings
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.secrets import Keys, load_keys
from app.core.tokens import MediaTokenClaims, decode_access_token, verify_media_token
from app.db.models import Library, LibraryAccess, User
from app.db.session import SessionDep

_bearer = HTTPBearer(auto_error=False, description="Hearth access token")


def get_app_settings(request: Request) -> Settings:
    """Return the settings instance bound to the running application."""
    return cast(Settings, request.app.state.settings)


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


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
    user = await session.get(User, claims.user_id)
    if user is None:
        raise UnauthorizedError("Invalid media token", code="invalid_media_token")
    if user.disabled:
        raise UnauthorizedError("Account is disabled", code="account_disabled")
    if user.token_version != claims.token_version:
        raise UnauthorizedError("Media token has been revoked", code="token_revoked")
    return user


MediaUser = Annotated[User, Depends(get_media_user)]


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

    if user.role == "admin":
        return library

    granted = await session.scalar(
        select(LibraryAccess.id).where(
            LibraryAccess.library_id == library.id,
            LibraryAccess.user_id == user.id,
        )
    )
    if granted is None:
        raise NotFoundError("Library not found", code="library_not_found")
    return library


LibraryAccessDep = Annotated[Library, Depends(require_library_access)]
