"""Library read endpoints and administrator CRUD."""

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.deps import LibraryAccessDep, RequireAdmin, RequireUser, SettingsDep
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.models import Library, LibraryAccess, LibraryPath
from app.db.session import SessionDep
from app.services.audit import record_audit
from app.services.libraries import validate_library_paths

router = APIRouter(prefix="/libraries", tags=["libraries"])

LibraryType = Literal["movie", "tv", "music", "book"]


class LibrarySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    type: str


class LibraryDetail(LibrarySummary):
    paths: list[str]


class LibraryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    type: LibraryType
    paths: list[str] = Field(min_length=1)


class LibraryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    type: LibraryType | None = None
    paths: list[str] | None = None


def _name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValidationError("Library name is required", code="invalid_library_name")
    return name


async def _ensure_unique_name(
    session: SessionDep, name: str, *, exclude_id: str | None = None
) -> None:
    query = select(Library.id).where(Library.name == name)
    if exclude_id is not None:
        query = query.where(Library.id != exclude_id)
    if await session.scalar(query) is not None:
        raise ConflictError("A library with that name already exists", code="library_name_taken")


def _detail(library: Library) -> LibraryDetail:
    return LibraryDetail(
        id=library.id,
        name=library.name,
        type=library.type,
        paths=[path.path for path in library.paths],
    )


@router.get("")
async def list_libraries(user: RequireUser, session: SessionDep) -> list[LibrarySummary]:
    """List the libraries visible to the current user."""
    if user.role == "admin":
        libraries = (await session.scalars(select(Library).order_by(Library.name))).all()
    else:
        libraries = (
            await session.scalars(
                select(Library)
                .join(LibraryAccess, LibraryAccess.library_id == Library.id)
                .where(LibraryAccess.user_id == user.id)
                .order_by(Library.name)
            )
        ).all()
    return [LibrarySummary.model_validate(library) for library in libraries]


@router.get("/{library_id}")
async def get_library(
    library: LibraryAccessDep, user: RequireUser
) -> LibrarySummary | LibraryDetail:
    """Return one library if the current user can access it.

    Root paths are only exposed to administrators; a normal user has no need
    for host filesystem layout and it may leak mount structure.
    """
    if user.role == "admin":
        return _detail(library)
    return LibrarySummary.model_validate(library)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_library(
    payload: LibraryCreate,
    actor: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> LibraryDetail:
    """Create a library with validated root paths."""
    name = _name(payload.name)
    await _ensure_unique_name(session, name)
    paths = validate_library_paths(settings, payload.paths)

    library = Library(name=name, type=payload.type)
    library.paths = [LibraryPath(path=path) for path in paths]
    session.add(library)
    await session.flush()
    record_audit(
        session,
        actor_id=actor.id,
        action="library.create",
        target_type="library",
        target_id=library.id,
        details={"name": name, "type": payload.type},
    )
    await session.commit()
    return _detail(library)


@router.patch("/{library_id}")
async def update_library(
    library_id: str,
    payload: LibraryUpdate,
    actor: RequireAdmin,
    session: SessionDep,
    settings: SettingsDep,
) -> LibraryDetail:
    """Rename, retype or replace the root paths of a library."""
    library = await session.get(Library, library_id)
    if library is None:
        raise NotFoundError("Library not found", code="library_not_found")

    if payload.name is not None:
        name = _name(payload.name)
        await _ensure_unique_name(session, name, exclude_id=library.id)
        library.name = name
    if payload.type is not None:
        library.type = payload.type
    if payload.paths is not None:
        paths = validate_library_paths(settings, payload.paths)
        library.paths = [LibraryPath(path=path) for path in paths]

    record_audit(
        session,
        actor_id=actor.id,
        action="library.update",
        target_type="library",
        target_id=library.id,
        details={"fields": sorted(payload.model_fields_set)},
    )
    await session.commit()
    return _detail(library)


@router.delete("/{library_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_library(library_id: str, actor: RequireAdmin, session: SessionDep) -> Response:
    """Delete a library and its grants. Media files on disk are untouched."""
    library = await session.get(Library, library_id)
    if library is None:
        raise NotFoundError("Library not found", code="library_not_found")

    record_audit(
        session,
        actor_id=actor.id,
        action="library.delete",
        target_type="library",
        target_id=library.id,
        details={"name": library.name},
    )
    await session.delete(library)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
