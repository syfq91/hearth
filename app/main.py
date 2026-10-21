"""FastAPI application factory and ASGI entrypoint."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
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

logger = logging.getLogger("hearth.main")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application.

    Passing explicit settings keeps the factory testable; production uses the
    cached environment-backed singleton.
    """
    resolved = settings or get_settings()
    setup_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "hearth starting",
            extra={"env": resolved.env, "version": __version__},
        )
        yield
        logger.info("hearth stopping")

    application = FastAPI(
        title="Hearth",
        description="Self-hosted personal media server",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = resolved

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
    return application


app = create_app()
