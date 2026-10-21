"""Structured JSON logging with request ID context."""

import json
import logging
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

# Context variable for tracking request ID across async tasks
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Retrieve current request ID from context."""
    return request_id_ctx.get()


def set_request_id(request_id: str | None) -> Token[str | None]:
    """Set request ID in context, returning token for reset."""
    return request_id_ctx.set(request_id)


class JSONFormatter(logging.Formatter):
    """Formatter that outputs structured JSON log entries."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the specified record as a JSON string."""
        log_obj: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


def setup_logging(log_level: str = "INFO") -> None:
    """Configure root logger with JSONFormatter and appropriate log level."""
    level = logging.getLevelName(log_level.upper())
    if not isinstance(level, int):
        raise ValueError(f"Invalid log level: {log_level!r}")

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    root_logger.addHandler(handler)
