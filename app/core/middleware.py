"""HTTP middleware: request IDs and structured access logs."""

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.errors import PayloadTooLargeError
from app.core.logging import reset_request_id, set_request_id

logger = logging.getLogger("hearth.http")

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Enforce a maximum size limit on incoming request bodies."""

    def __init__(self, app: ASGIApp, max_bytes: int = 50 * 1024 * 1024) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
                if length > self.max_bytes:
                    err = PayloadTooLargeError(
                        f"Request body exceeds maximum allowed size of {self.max_bytes} bytes",
                        details={"max_bytes": self.max_bytes, "content_length": length},
                    )
                    from fastapi.responses import JSONResponse

                    return JSONResponse(status_code=err.status_code, content=err.to_dict())
            except ValueError:
                pass
        return await call_next(request)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a request ID to the logging context and response headers.

    A caller-supplied ``X-Request-ID`` is honored only when it is short and
    contains safe characters; otherwise a fresh ID is generated. This keeps
    untrusted headers from injecting arbitrary content into logs.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process one request and emit an access log line."""
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex

        token = set_request_id(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)

            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            return response
        except Exception:
            logger.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        finally:
            reset_request_id(token)
