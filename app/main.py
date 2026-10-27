"""FastAPI application factory and ASGI entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.staticfiles import StaticFiles
from starlette.types import ExceptionHandler

from app import __version__
from app.api.v1 import router as api_v1_router
from app.core.config import Settings, get_settings
from app.core.errors import (
    AppError,
    app_error_handler,
    http_exception_handler,
    unhandled_exception_handler,
    validation_error_handler,
)
from app.core.logging import setup_logging
from app.core.middleware import RequestIDMiddleware
from app.core.ratelimit import SlidingWindowLimiter
from app.db.migrate import run_migrations_async
from app.db.session import build_engine, build_sessionmaker
from app.services.transcode.session import get_session_manager

logger = logging.getLogger("hearth.main")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application.

    Passing explicit settings keeps the factory testable; production uses the
    cached environment-backed singleton.
    """
    resolved = settings or get_settings()
    setup_logging(resolved.log_level)
    engine = build_engine(resolved)
    sessionmaker = build_sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Migrations run behind a file lock so concurrent API/worker starts do
        # not race. They are executed in a worker thread because Alembic's env
        # owns its own event loop.
        await run_migrations_async(resolved)
        get_session_manager(resolved).startup_sweep()
        logger.info(
            "hearth starting",
            extra={"env": resolved.env, "version": __version__},
        )
        yield
        logger.info("hearth stopping")
        await get_session_manager(resolved).stop_all()
        await engine.dispose()

    application = FastAPI(
        title="Hearth",
        description="Self-hosted personal media server",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = resolved
    application.state.engine = engine
    application.state.sessionmaker = sessionmaker
    application.state.login_limiter = SlidingWindowLimiter(
        limit=resolved.login_rate_limit_attempts,
        window_seconds=resolved.login_rate_limit_window_seconds,
    )

    application.add_middleware(RequestIDMiddleware)

    application.add_exception_handler(AppError, cast(ExceptionHandler, app_error_handler))
    application.add_exception_handler(
        RequestValidationError, cast(ExceptionHandler, validation_error_handler)
    )
    application.add_exception_handler(
        StarletteHTTPException, cast(ExceptionHandler, http_exception_handler)
    )
    application.add_exception_handler(
        Exception, cast(ExceptionHandler, unhandled_exception_handler)
    )

    application.include_router(api_v1_router)

    testplayer = _testplayer_directory()
    if testplayer is not None:
        application.mount(
            "/testplayer",
            StaticFiles(directory=testplayer, html=True),
            name="testplayer",
        )
    return application


def _testplayer_directory() -> Path | None:
    """Return the developer test player when running from a source checkout."""
    candidates = (
        Path.cwd() / "tools" / "testplayer",
        Path(__file__).resolve().parents[1] / "tools" / "testplayer",
    )
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


app = create_app()
