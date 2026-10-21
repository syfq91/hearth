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

    # Authentication. When secret_key is unset a random key is generated once
    # and persisted in data_dir with mode 0600.
    secret_key: str | None = None
    jwt_issuer: str = "hearth"
    jwt_audience: str = "hearth-api"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    media_token_seconds: int = 6 * 60 * 60

    # Accounts
    max_enabled_users: int = 15
    password_min_length: int = 8

    # Jobs
    job_poll_interval: float = 1.0
    job_worker_concurrency: int = 4

    @property
    def database_url(self) -> str:
        """SQLAlchemy async URL for the configured SQLite database."""
        return f"sqlite+aiosqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
