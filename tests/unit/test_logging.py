"""Unit tests for structured JSON logging."""

import json
import logging
from io import StringIO

from app.core.logging import (
    JSONFormatter,
    RingBufferLogHandler,
    get_recent_logs,
    get_request_id,
    ring_buffer_handler,
    set_request_id,
    setup_logging,
)


def test_json_formatter_basic() -> None:
    """Verify log record outputs structured JSON with required keys."""
    formatter = JSONFormatter()
    logger = logging.getLogger("test_basic")
    record = logger.makeRecord(
        name="test_basic",
        level=logging.INFO,
        fn="test_logging.py",
        lno=15,
        msg="Test message",
        args=(),
        exc_info=None,
    )
    output = formatter.format(record)
    parsed = json.loads(output)

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test_basic"
    assert parsed["message"] == "Test message"
    assert "timestamp" in parsed
    assert parsed["request_id"] is None


def test_json_formatter_with_request_id() -> None:
    """Verify request_id is captured in log output when set in context."""
    formatter = JSONFormatter()
    set_request_id("req-12345")
    try:
        assert get_request_id() == "req-12345"
        logger = logging.getLogger("test_req")
        record = logger.makeRecord(
            name="test_req",
            level=logging.WARNING,
            fn="test_logging.py",
            lno=35,
            msg="Request specific warning",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["request_id"] == "req-12345"
    finally:
        set_request_id(None)


def test_stream_output() -> None:
    """Verify formatted log output writes to stream."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    logger = logging.getLogger("test_stream")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("Hello from stream")
    output = stream.getvalue().strip()
    parsed = json.loads(output)
    assert parsed["message"] == "Hello from stream"


def test_setup_logging_configures_root_logger() -> None:
    """Verify setup_logging installs JSON stream and ring buffer handlers at the requested level."""
    setup_logging("debug")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 2
    stream_handlers = [h for h in root.handlers if type(h) is logging.StreamHandler]
    assert len(stream_handlers) == 1
    assert isinstance(stream_handlers[0].formatter, JSONFormatter)
    assert any(isinstance(h, RingBufferLogHandler) for h in root.handlers)


def test_setup_logging_rejects_unknown_level() -> None:
    """Verify invalid log levels fail loudly instead of silently defaulting."""
    try:
        setup_logging("LOUD")
    except ValueError as exc:
        assert "LOUD" in str(exc)
    else:
        raise AssertionError("Expected ValueError for an invalid log level")


def test_get_recent_logs_categories_and_filters() -> None:
    """Verify get_recent_logs properly filters by category, level, and search."""
    scanner_logger = logging.getLogger("app.services.scanner.test")
    scanner_logger.addHandler(ring_buffer_handler)
    scanner_logger.setLevel(logging.INFO)

    transcode_logger = logging.getLogger("app.services.transcode.test")
    transcode_logger.addHandler(ring_buffer_handler)
    transcode_logger.setLevel(logging.INFO)

    middleware_logger = logging.getLogger("app.core.middleware")
    middleware_logger.addHandler(ring_buffer_handler)
    middleware_logger.setLevel(logging.INFO)

    scanner_logger.info("Scanning completed for Movies", extra={"scanned": 42})
    scanner_logger.error("Scanning failed on corrupted file")
    transcode_logger.info("Transcoding session started", extra={"session_id": "sess-99"})
    middleware_logger.info("GET /api/v1/health HTTP/1.1", extra={"status_code": 200})

    # Test category scanning
    scan_logs = get_recent_logs(category="scanning", limit=50)
    assert any("Scanning completed for Movies" in entry["message"] for entry in scan_logs)
    assert not any("Transcoding session started" in entry["message"] for entry in scan_logs)

    # Test category transcoding
    tx_logs = get_recent_logs(category="transcoding", limit=50)
    assert any("Transcoding session started" in entry["message"] for entry in tx_logs)
    assert not any("Scanning completed" in entry["message"] for entry in tx_logs)

    # Test category activity
    act_logs = get_recent_logs(category="activity", limit=50)
    assert any("/api/v1/health" in entry["message"] for entry in act_logs)

    # Test category error
    err_logs = get_recent_logs(category="error", limit=50)
    assert any("Scanning failed on corrupted file" in entry["message"] for entry in err_logs)
    assert all(
        entry["level"] in ("ERROR", "CRITICAL") or entry.get("exception") for entry in err_logs
    )
