"""HTTP middleware: request IDs and structured access logs."""

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import reset_request_id, set_request_id

logger = logging.getLogger("hearth.http")

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


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
