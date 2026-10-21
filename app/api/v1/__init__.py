"""API v1 package."""

from fastapi import APIRouter

from app.api.v1 import auth, system

router = APIRouter(prefix="/api/v1")
router.include_router(system.router)
router.include_router(auth.router)

__all__ = ["router"]
