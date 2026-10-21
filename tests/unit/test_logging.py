"""Unit tests for structured JSON logging."""

import json
import logging
from io import StringIO

from app.core.logging import JSONFormatter, get_request_id, set_request_id, setup_logging


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
    """Verify setup_logging installs a single JSON handler at the requested level."""
    setup_logging("debug")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JSONFormatter)


def test_setup_logging_rejects_unknown_level() -> None:
    """Verify invalid log levels fail loudly instead of silently defaulting."""
    try:
        setup_logging("LOUD")
    except ValueError as exc:
        assert "LOUD" in str(exc)
    else:
        raise AssertionError("Expected ValueError for an invalid log level")
