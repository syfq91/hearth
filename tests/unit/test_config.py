"""Unit tests for core configuration settings."""

import os
from unittest import mock

from app.core.config import Settings, get_settings


def test_default_settings() -> None:
    """Verify default setting values when no env vars provided."""
    settings = Settings()
    assert settings.env == "development"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.log_level == "INFO"
    assert str(settings.data_dir).endswith("config") or settings.data_dir.name == "config"
    assert settings.render_device.name == "renderD128"


def test_env_override() -> None:
    """Verify settings parse HEARTH_ prefixed environment variables."""
    with mock.patch.dict(
        os.environ,
        {
            "HEARTH_ENV": "production",
            "HEARTH_PORT": "9000",
            "HEARTH_LOG_LEVEL": "DEBUG",
        },
    ):
        settings = Settings()
        assert settings.env == "production"
        assert settings.port == 9000
        assert settings.log_level == "DEBUG"


def test_get_settings_cached() -> None:
    """Verify get_settings returns consistent settings instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
