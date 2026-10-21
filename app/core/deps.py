"""Shared FastAPI dependencies."""

from typing import Annotated, cast

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.secrets import Keys, load_keys
from app.core.tokens import decode_access_token
from app.db.models import User
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
