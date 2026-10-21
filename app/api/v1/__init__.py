"""API v1 package."""

from fastapi import APIRouter

from app.api.v1 import system

router = APIRouter(prefix="/api/v1")
router.include_router(system.router)

__all__ = ["router"]
