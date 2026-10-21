"""API v1 package."""

from fastapi import APIRouter

from app.api.v1 import admin, auth, events, jobs, libraries, metadata, system, users

router = APIRouter(prefix="/api/v1")
router.include_router(system.router)
router.include_router(auth.router)
router.include_router(libraries.router)
router.include_router(users.router)
router.include_router(admin.router)
router.include_router(jobs.router)
router.include_router(events.router)
router.include_router(metadata.router)

__all__ = ["router"]
