"""Administrator user management."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import RequireAdmin, SettingsDep
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.passwords import hash_password, validate_password, validate_username
from app.db.models import Library, LibraryAccess, RefreshToken, User
from app.db.session import SessionDep
from app.services.audit import record_audit

router = APIRouter(prefix="/users", tags=["users"])

Role = Literal["admin", "user"]


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    role: Role = "user"
    max_bitrate: int | None = Field(default=None, ge=1)
    allow_transcode: bool = True
    max_rating: str | None = Field(default=None, max_length=16)
    library_ids: list[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=64)
    role: Role | None = None
    disabled: bool | None = None
    max_bitrate: int | None = Field(default=None, ge=1)
    allow_transcode: bool | None = None
    max_rating: str | None = Field(default=None, max_length=16)
    library_ids: list[str] | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class UserDetail(BaseModel):
    id: str
    username: str
    role: str
    disabled: bool
    max_bitrate: int | None
    allow_transcode: bool
    max_rating: str | None
    created_at: datetime
    updated_at: datetime
    library_ids: list[str]


async def _grants_by_user(session: AsyncSession, user_id: str) -> list[str]:
    rows = await session.scalars(
        select(LibraryAccess.library_id)
        .where(LibraryAccess.user_id == user_id)
        .order_by(LibraryAccess.library_id)
    )
    return list(rows)


async def _detail(session: AsyncSession, user: User) -> UserDetail:
    return UserDetail(
        id=user.id,
        username=user.username,
        role=user.role,
        disabled=user.disabled,
        max_bitrate=user.max_bitrate,
        allow_transcode=user.allow_transcode,
        max_rating=user.max_rating,
        created_at=user.created_at,
        updated_at=user.updated_at,
        library_ids=await _grants_by_user(session, user.id),
    )


async def _load_user(session: AsyncSession, user_id: str) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found", code="user_not_found")
    return user


async def _validate_library_ids(session: AsyncSession, library_ids: list[str]) -> list[str]:
    unique = list(dict.fromkeys(library_ids))
    if not unique:
        return []
    found = set((await session.scalars(select(Library.id).where(Library.id.in_(unique)))).all())
    missing = [library_id for library_id in unique if library_id not in found]
    if missing:
        raise NotFoundError(
            "One or more libraries do not exist",
            code="library_not_found",
            details={"missing": missing},
        )
    return unique


async def _replace_grants(session: AsyncSession, user_id: str, library_ids: list[str]) -> None:
    await session.execute(delete(LibraryAccess).where(LibraryAccess.user_id == user_id))
    for library_id in library_ids:
        session.add(LibraryAccess(library_id=library_id, user_id=user_id))


async def _enabled_user_count(session: AsyncSession) -> int:
    return int(
        await session.scalar(select(func.count()).select_from(User).where(User.disabled.is_(False)))
        or 0
    )


async def _enabled_admin_count(session: AsyncSession) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.disabled.is_(False))
        )
        or 0
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    actor: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> UserDetail:
    """Create an account with options and optional library grants."""
    username = validate_username(payload.username)
    validate_password(payload.password, settings)
    library_ids = await _validate_library_ids(session, payload.library_ids)

    existing = await session.scalar(select(User.id).where(User.username == username))
    if existing is not None:
        raise ConflictError("Username is already taken", code="username_taken")

    if await _enabled_user_count(session) >= settings.max_enabled_users:
        raise ConflictError(
            f"At most {settings.max_enabled_users} enabled users are allowed",
            code="user_limit_reached",
        )

    user = User(
        username=username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        max_bitrate=payload.max_bitrate,
        allow_transcode=payload.allow_transcode,
        max_rating=payload.max_rating,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError("Username is already taken", code="username_taken") from exc

    await _replace_grants(session, user.id, library_ids)
    record_audit(
        session,
        actor_id=actor.id,
        action="user.create",
        target_type="user",
        target_id=user.id,
        details={"username": user.username, "role": user.role},
    )
    await session.commit()
    return await _detail(session, user)


@router.get("")
async def list_users(_actor: RequireAdmin, session: SessionDep) -> list[UserDetail]:
    """List all accounts."""
    users = (await session.scalars(select(User).order_by(User.username))).all()
    return [await _detail(session, user) for user in users]


@router.get("/{user_id}")
async def get_user(user_id: str, _actor: RequireAdmin, session: SessionDep) -> UserDetail:
    """Return one account."""
    return await _detail(session, await _load_user(session, user_id))


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    payload: UserUpdate,
    actor: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> UserDetail:
    """Edit username, role, options, enabled state and library grants."""
    user = await _load_user(session, user_id)
    changes = payload.model_fields_set
    was_disabled = user.disabled

    if "username" in changes and payload.username is not None:
        username = validate_username(payload.username)
        clash = await session.scalar(
            select(User.id).where(User.username == username, User.id != user.id)
        )
        if clash is not None:
            raise ConflictError("Username is already taken", code="username_taken")
        user.username = username

    removing_admin = user.role == "admin" and (payload.role == "user" or payload.disabled is True)
    if removing_admin and user.id == actor.id:
        raise ForbiddenError(
            "Administrators cannot disable or demote their own account",
            code="cannot_modify_self",
        )
    if removing_admin and await _enabled_admin_count(session) <= 1:
        raise ConflictError(
            "At least one enabled administrator is required",
            code="last_admin",
        )

    if payload.disabled is not None:
        if payload.disabled and not was_disabled:
            # Invalidate access tokens after re-enabling and kill refresh tokens.
            user.token_version += 1
            await session.execute(
                update(RefreshToken)
                .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
                .values(revoked_at=datetime.now(UTC), revoked_reason="account_disabled")
            )
        if (
            not payload.disabled
            and was_disabled
            and (await _enabled_user_count(session) >= settings.max_enabled_users)
        ):
            raise ConflictError(
                f"At most {settings.max_enabled_users} enabled users are allowed",
                code="user_limit_reached",
            )
        user.disabled = payload.disabled

    if payload.role is not None:
        user.role = payload.role
    if "max_bitrate" in changes:
        user.max_bitrate = payload.max_bitrate
    if payload.allow_transcode is not None:
        user.allow_transcode = payload.allow_transcode
    if "max_rating" in changes:
        user.max_rating = payload.max_rating

    if payload.library_ids is not None:
        library_ids = await _validate_library_ids(session, payload.library_ids)
        await _replace_grants(session, user.id, library_ids)

    record_audit(
        session,
        actor_id=actor.id,
        action="user.update",
        target_type="user",
        target_id=user.id,
        details={"fields": sorted(changes)},
    )
    await session.commit()
    return await _detail(session, user)


@router.post("/{user_id}/password")
async def reset_password(
    user_id: str,
    payload: PasswordReset,
    actor: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> UserDetail:
    """Reset a password and revoke every existing session for that account."""
    user = await _load_user(session, user_id)
    validate_password(payload.password, settings)

    user.password_hash = hash_password(payload.password)
    user.token_version += 1
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC), revoked_reason="password_reset")
    )
    record_audit(
        session,
        actor_id=actor.id,
        action="user.password_reset",
        target_type="user",
        target_id=user.id,
        details={"username": user.username},
    )
    await session.commit()
    return await _detail(session, user)
