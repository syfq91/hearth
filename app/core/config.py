"""Application configuration and settings."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Hearth application settings parsed from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="HEARTH_",
        case_sensitive=False,
        env_file=".env",
        extra="ignore",
    )

    env: Literal["development", "production", "testing"] = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # Filesystem directories
    data_dir: Path = Path("/config")
    transcode_dir: Path = Path("/transcode")
    media_dir: Path = Path("/media")
    db_path: Path = Path("/config/hearth.db")

    # Hardware devices
    render_device: Path = Path("/dev/dri/renderD128")


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
