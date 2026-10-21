"""Unified application error types and FastAPI exception handlers."""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("hearth.errors")


class AppError(Exception):
    """Base application exception with standardized code and details."""

    def __init__(
        self,
        message: str,
        code: str = "app_error",
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        """Convert error to standard JSON API error representation."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class NotFoundError(AppError):
    """Resource not found error (404)."""

    def __init__(
        self,
        message: str = "Resource not found",
        code: str = "not_found",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=status.HTTP_404_NOT_FOUND,
            details=details,
        )


class ValidationError(AppError):
    """Data validation or schema error (422)."""

    def __init__(
        self,
        message: str = "Validation failed",
        code: str = "validation_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            details=details,
        )


class ConflictError(AppError):
    """Resource conflict error (409)."""

    def __init__(
        self,
        message: str = "Resource conflict",
        code: str = "conflict",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=status.HTTP_409_CONFLICT,
            details=details,
        )


class UnauthorizedError(AppError):
    """Authentication required or failed error (401)."""

    def __init__(
        self,
        message: str = "Authentication required",
        code: str = "unauthorized",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=status.HTTP_401_UNAUTHORIZED,
            details=details,
        )


class ForbiddenError(AppError):
    """Permission denied error (403)."""

    def __init__(
        self,
        message: str = "Permission denied",
        code: str = "forbidden",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=status.HTTP_403_FORBIDDEN,
            details=details,
        )


class InternalError(AppError):
    """Internal server error (500)."""

    def __init__(
        self,
        message: str = "An unexpected error occurred",
        code: str = "internal_server_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(
            message=message,
            code=code,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            details=details,
        )


async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    """Handle custom application exceptions."""
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle FastAPI request validation errors in unified shape."""
    details = {"errors": jsonable_encoder(exc.errors())}
    error = ValidationError(message="Invalid request parameters", details=details)
    return JSONResponse(status_code=error.status_code, content=error.to_dict())


async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handle framework HTTP errors (404, 405, ...) in unified shape."""
    try:
        code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
    except ValueError:
        code = "http_error"

    message = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    error = AppError(message=message, code=code, status_code=exc.status_code)
    response = JSONResponse(status_code=error.status_code, content=error.to_dict())
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Handle any uncaught exceptions in unified shape."""
    logger.exception("Unhandled server exception: %s", exc)
    error = InternalError(message="An unexpected error occurred")
    return JSONResponse(status_code=error.status_code, content=error.to_dict())
