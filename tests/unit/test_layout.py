"""Verify package structure and initialization."""

import app


def test_app_version() -> None:
    """Verify package exposes version."""
    assert app.__version__ == "0.1.0"
