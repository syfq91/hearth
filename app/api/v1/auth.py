"""Authentication endpoints: first-run setup, login, refresh, logout and me."""

from datetime import UTC, datetime
from functools import lru_cache
from typing import Any, cast

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.deps import CurrentUser, KeysDep, SettingsDep
from app.core.errors import ConflictError, ForbiddenError, UnauthorizedError
from app.core.ids import uuid7
from app.core.passwords import (
    hash_password,
    validate_password,
    validate_username,
    verify_password,
)
from app.core.secrets import Keys
from app.core.tokens import (
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    refresh_expiry,
)
from app.db.models import RefreshToken, User
from app.db.session import SessionDep
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=256)


class TokenPair(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    refresh_expires_in: int


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    role: str
    disabled: bool
    max_bitrate: int | None
    allow_transcode: bool
    max_rating: str | None
    created_at: datetime


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A real hash used to equalize timing when the username is unknown."""
    return hash_password("hearth-timing-equalization-placeholder")


def _issue_pair(
    session: AsyncSession,
    user: User,
    *,
    settings: Settings,
    keys: Keys,
    user_agent: str | None,
    family_id: str | None = None,
) -> tuple[TokenPair, RefreshToken]:
    """Create a refresh-token row and a fresh access token."""
    raw_refresh = generate_refresh_token()
    row = RefreshToken(
        user_id=user.id,
        family_id=family_id or uuid7(),
        token_hash=hash_refresh_token(raw_refresh),
        expires_at=refresh_expiry(settings),
        user_agent=user_agent[:255] if user_agent else None,
    )
    session.add(row)
    access_token, _expires_at = create_access_token(
        user_id=user.id,
        role=user.role,
        token_version=user.token_version,
        settings=settings,
        key=keys.jwt,
    )
    pair = TokenPair(
        access_token=access_token,
        expires_in=settings.access_token_minutes * 60,
        refresh_token=raw_refresh,
        refresh_expires_in=settings.refresh_token_days * 24 * 60 * 60,
    )
    return pair, row


async def _revoke_family(
    session: AsyncSession, family_id: str, *, reason: str, now: datetime
) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )


@router.post("/setup", status_code=status.HTTP_201_CREATED)
async def setup(
    payload: Credentials,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    keys: KeysDep,
) -> TokenPair:
    """Create the first administrator. Only allowed while no accounts exist."""
    user_count = await session.scalar(select(func.count()).select_from(User))
    if user_count:
        raise ConflictError(
            "Initial setup has already been completed",
            code="setup_complete",
        )

    username = validate_username(payload.username)
    validate_password(payload.password, settings)

    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        role="admin",
    )
    session.add(user)
    await session.flush()
    record_audit(
        session,
        actor_id=user.id,
        action="auth.setup",
        target_type="user",
        target_id=user.id,
        details={"username": username},
    )
    pair, _row = _issue_pair(
        session,
        user,
        settings=settings,
        keys=keys,
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()
    return pair


@router.post("/login")
async def login(
    payload: Credentials,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    keys: KeysDep,
) -> TokenPair:
    """Authenticate with username and password and issue a token pair."""
    username = payload.username.strip().lower()
    user = await session.scalar(select(User).where(User.username == username))
    if user is None:
        # Verify against a real hash so unknown usernames are not cheaper.
        verify_password(_dummy_hash(), payload.password)
        raise UnauthorizedError("Invalid username or password", code="invalid_credentials")
    if not verify_password(user.password_hash, payload.password):
        raise UnauthorizedError("Invalid username or password", code="invalid_credentials")
    if user.disabled:
        raise ForbiddenError("Account is disabled", code="account_disabled")

    user.last_login_at = datetime.now(UTC)
    pair, _row = _issue_pair(
        session,
        user,
        settings=settings,
        keys=keys,
        user_agent=request.headers.get("user-agent"),
    )
    await session.commit()
    return pair


@router.post("/refresh")
async def refresh(
    payload: RefreshRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    keys: KeysDep,
) -> TokenPair:
    """Rotate a refresh token.

    Presenting an already-revoked token is treated as theft: every live token
    in that family is revoked before returning 401.
    """
    now = datetime.now(UTC)
    token_hash = hash_refresh_token(payload.refresh_token)
    row = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if row is None:
        raise UnauthorizedError("Invalid refresh token", code="invalid_refresh_token")

    if row.revoked_at is not None:
        await _revoke_family(session, row.family_id, reason="reuse_detected", now=now)
        await session.commit()
        raise UnauthorizedError(
            "Refresh token reuse detected; all sessions revoked",
            code="refresh_token_reuse",
        )

    if row.expires_at <= now:
        row.revoked_at = now
        row.revoked_reason = "expired"
        await session.commit()
        raise UnauthorizedError("Refresh token expired", code="invalid_refresh_token")

    user = await session.get(User, row.user_id)
    if user is None or user.disabled:
        await _revoke_family(session, row.family_id, reason="account_disabled", now=now)
        await session.commit()
        raise UnauthorizedError("Account is unavailable", code="account_disabled")

    # Conditional update closes the race where two refreshes present the same
    # token concurrently; exactly one can win.
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == row.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now, revoked_reason="rotated")
        ),
    )
    if result.rowcount != 1:
        await _revoke_family(session, row.family_id, reason="reuse_detected", now=now)
        await session.commit()
        raise UnauthorizedError(
            "Refresh token reuse detected; all sessions revoked",
            code="refresh_token_reuse",
        )

    pair, _new_row = _issue_pair(
        session,
        user,
        settings=settings,
        keys=keys,
        user_agent=request.headers.get("user-agent"),
        family_id=row.family_id,
    )
    await session.commit()
    return pair


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: RefreshRequest,
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    """Revoke the presented refresh-token family for the current user."""
    token_hash = hash_refresh_token(payload.refresh_token)
    row = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    if row is not None and row.user_id == user.id:
        await _revoke_family(session, row.family_id, reason="logout", now=datetime.now(UTC))
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
async def me(user: CurrentUser) -> UserResponse:
    """Return the authenticated account."""
    return UserResponse.model_validate(user)
