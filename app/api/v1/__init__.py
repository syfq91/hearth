"""API v1 package."""

from fastapi import APIRouter

from app.api.v1 import auth, libraries, system, users

router = APIRouter(prefix="/api/v1")
router.include_router(system.router)
router.include_router(auth.router)
router.include_router(libraries.router)
router.include_router(users.router)

__all__ = ["router"]
