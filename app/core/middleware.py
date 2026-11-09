"""HTTP middleware: request IDs and structured access logs."""

import logging
import re
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import PayloadTooLargeError
from app.core.logging import reset_request_id, set_request_id
from app.core.metrics import record_http_request

logger = logging.getLogger("hearth.http")

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


class RequestSizeLimitMiddleware:
    """Enforce a maximum size limit on incoming request bodies."""

    def __init__(self, app: ASGIApp, max_bytes: int = 50 * 1024 * 1024) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
                if length > self.max_bytes:
                    err = PayloadTooLargeError(
                        f"Request body exceeds maximum allowed size of {self.max_bytes} bytes",
                        details={"max_bytes": self.max_bytes, "content_length": length},
                    )
                    from fastapi.responses import JSONResponse

                    response = JSONResponse(status_code=err.status_code, content=err.to_dict())
                    await response(scope, receive, send)
                    return
            except ValueError:
                pass

        await self.app(scope, receive, send)


class RequestIDMiddleware:
    """Attach a request ID to the logging context and response headers.

    A caller-supplied ``X-Request-ID`` is honored only when it is short and
    contains safe characters; otherwise a fresh ID is generated. This keeps
    untrusted headers from injecting arbitrary content into logs.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        incoming = headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex

        token = set_request_id(request_id)
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                res_headers = MutableHeaders(scope=message)
                res_headers["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            record_http_request(scope["method"], status_code)
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.exception(
                "request failed",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "duration_ms": duration_ms,
                },
            )
            raise
        finally:
            reset_request_id(token)


class SecurityHeadersMiddleware:
    """Inject baseline defensive headers into every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                res_headers = MutableHeaders(scope=message)
                res_headers.setdefault("X-Content-Type-Options", "nosniff")
                res_headers.setdefault("X-Frame-Options", "SAMEORIGIN")
                res_headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
            await send(message)

        await self.app(scope, receive, send_wrapper)
