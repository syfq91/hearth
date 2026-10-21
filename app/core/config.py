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

    # Login throttling
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 60
    trust_proxy_headers: bool = False

    # Jobs
    job_poll_interval: float = 1.0
    job_worker_concurrency: int = 4
    job_retry_base_seconds: float = 2.0
    job_retry_max_seconds: float = 300.0

    # Media probing
    ffprobe_binary: str = "ffprobe"
    ffprobe_timeout_seconds: float = 30.0
    ffprobe_concurrency: int = 4

    # Scanning
    scan_mass_delete_threshold: float = 0.30
    scan_mass_delete_min_files: int = 10
    scan_missing_grace_hours: float = 24.0

    # Metadata providers
    tmdb_api_key: str | None = None
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_image_base_url: str = "https://image.tmdb.org/t/p"
    tmdb_timeout_seconds: float = 10.0
    tmdb_cache_ttl_hours: float = 168.0
    tmdb_min_interval_seconds: float = 0.05
    metadata_match_threshold: float = 0.80

    @property
    def database_url(self) -> str:
        """SQLAlchemy async URL for the configured SQLite database."""
        return f"sqlite+aiosqlite:///{self.db_path}"


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
