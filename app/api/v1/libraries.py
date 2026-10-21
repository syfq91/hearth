"""Library read endpoints.

Admin write endpoints arrive with library CRUD; this router exposes the
per-user visibility rules that every later item/stream endpoint will reuse.
"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.deps import LibraryAccessDep, RequireUser
from app.db.models import Library, LibraryAccess
from app.db.session import SessionDep

router = APIRouter(prefix="/libraries", tags=["libraries"])


class LibrarySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    type: str


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
async def get_library(library: LibraryAccessDep) -> LibrarySummary:
    """Return one library if the current user can access it."""
    return LibrarySummary.model_validate(library)
