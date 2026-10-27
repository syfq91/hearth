"""API v1 package."""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    books,
    collections,
    events,
    hls,
    home,
    images,
    items,
    jobs,
    libraries,
    metadata,
    music,
    playback,
    playlists,
    search,
    shows,
    stream,
    subtitles,
    system,
    users,
)

router = APIRouter(prefix="/api/v1")
router.include_router(system.router)
router.include_router(auth.router)
router.include_router(libraries.router)
router.include_router(users.router)
router.include_router(admin.router)
router.include_router(jobs.router)
router.include_router(events.router)
router.include_router(metadata.router)
router.include_router(items.router)
router.include_router(collections.router)
router.include_router(playlists.router)
router.include_router(search.router)
router.include_router(shows.router)
router.include_router(music.router)
router.include_router(books.router)
router.include_router(home.router)
router.include_router(playback.router)
router.include_router(images.router)
router.include_router(stream.router)
router.include_router(hls.router)
router.include_router(subtitles.router)

__all__ = ["router"]
