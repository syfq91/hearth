"""Structured JSON logging with request ID context."""

import json
import logging
import sys
import threading
from collections import deque
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

# Context variable for tracking request ID across async tasks
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes that every LogRecord carries; anything else came from ``extra``.
_RESERVED_LOG_RECORD_KEYS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}


def get_request_id() -> str | None:
    """Retrieve current request ID from context."""
    return request_id_ctx.get()


def set_request_id(request_id: str | None) -> Token[str | None]:
    """Set request ID in context, returning token for reset."""
    return request_id_ctx.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the previous request ID from a token returned by set_request_id."""
    request_id_ctx.reset(token)


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

        # Promote ``logger.info(..., extra={...})`` fields to top-level JSON keys.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_KEYS and not key.startswith("_"):
                log_obj[key] = value

        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj, default=str)


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
    root_logger.addHandler(ring_buffer_handler)


_LOG_BUFFER: deque[dict[str, Any]] = deque(maxlen=2000)
_LOG_LOCK = threading.Lock()


class RingBufferLogHandler(logging.Handler):
    """Retains the most recent log records in memory for administrative tailing."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry: dict[str, Any] = {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "request_id": get_request_id(),
            }
            if record.exc_info:
                entry["exception"] = (
                    self.formatException(record.exc_info)  # type: ignore[attr-defined]
                    if self.formatter
                    else str(record.exc_info)
                )
            with _LOG_LOCK:
                _LOG_BUFFER.append(entry)
        except Exception:
            self.handleError(record)


ring_buffer_handler = RingBufferLogHandler()


def get_recent_logs(limit: int = 100, level: str | None = None) -> list[dict[str, Any]]:
    """Return recent log entries in reverse chronological order (newest first)."""
    with _LOG_LOCK:
        logs = list(_LOG_BUFFER)
    if level:
        lvl_upper = level.upper()
        logs = [entry for entry in logs if entry.get("level") == lvl_upper]
    return list(reversed(logs))[:limit]
