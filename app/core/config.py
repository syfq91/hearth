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
    vainfo_binary: str = "vainfo"
    ffmpeg_binary: str = "ffmpeg"

    # Transcoding
    transcode_force_software: bool = False
    transcode_max_hw_sessions: int = 2
    transcode_max_sw_sessions: int = 2
    transcode_idle_timeout_seconds: float = 60.0
    transcode_segment_duration_seconds: int = 4
    transcode_first_segment_timeout_seconds: float = 15.0
    transcode_audio_bitrate_kbps: int = 192
    transcode_test_encode_timeout_seconds: float = 5.0

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

    # Direct-play streaming
    stream_chunk_bytes: int = 512 * 1024

    # Subtitles
    subtitle_max_bytes: int = 4 * 1024 * 1024

    # Playback state
    watched_threshold: float = 0.90
    playback_session_gap_seconds: int = 300

    # Scanning
    scan_mass_delete_threshold: float = 0.30
    scan_mass_delete_min_files: int = 10
    scan_missing_grace_hours: float = 24.0
    scan_interval_hours: float = 6.0
    scan_scheduler_enabled: bool = True
    scan_watch_enabled: bool = True
    scan_watch_debounce_seconds: float = 10.0
    scan_watch_refresh_seconds: float = 60.0
    scan_scheduler_wake_seconds: float = 30.0

    # Metadata providers
    tmdb_api_key: str | None = None
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_image_base_url: str = "https://image.tmdb.org/t/p"
    tmdb_timeout_seconds: float = 10.0
    tmdb_cache_ttl_hours: float = 168.0
    tmdb_min_interval_seconds: float = 0.05
    metadata_match_threshold: float = 0.80
    metadata_retry_base_hours: float = 6.0
    metadata_retry_max_hours: float = 72.0
    metadata_unmatched_retry_hours: float = 24.0

    # Music metadata
    musicbrainz_enabled: bool = True
    musicbrainz_base_url: str = "https://musicbrainz.org/ws/2"
    musicbrainz_timeout_seconds: float = 10.0
    musicbrainz_rate_limit_seconds: float = 1.0
    musicbrainz_user_agent: str = (
        "HearthMediaServer/0.1.0 ( https://github.com/hearth-media/hearth )"
    )
    musicbrainz_cache_ttl_hours: float = 168.0
    cover_art_archive_base_url: str = "https://coverartarchive.org"
    listenbrainz_enabled: bool = True
    listenbrainz_base_url: str = "https://api.listenbrainz.org"

    # Book metadata
    openlibrary_enabled: bool = True
    openlibrary_base_url: str = "https://openlibrary.org"
    openlibrary_covers_base_url: str = "https://covers.openlibrary.org"
    google_books_enabled: bool = True
    google_books_base_url: str = "https://www.googleapis.com/books/v1"

    # Images
    image_cache_dir: Path | None = None
    image_download_timeout_seconds: float = 20.0
    image_max_download_bytes: int = 20 * 1024 * 1024
    image_variant_quality: int = 82
    image_blurhash_components: int = 4
    image_process_workers: int = 2
    image_allowed_hosts: list[str] = [
        "image.tmdb.org",
        "coverartarchive.org",
        "archive.org",
        "covers.openlibrary.org",
        "books.google.com",
    ]

    @property
    def database_url(self) -> str:
        """SQLAlchemy async URL for the configured SQLite database."""
        return f"sqlite+aiosqlite:///{self.db_path}"

    @property
    def images_dir(self) -> Path:
        """Root directory for cached artwork originals and variants."""
        return self.image_cache_dir or (self.data_dir / "cache" / "images")

    @property
    def comic_cache_dir(self) -> Path:
        """Root directory for extracted and resized comic pages."""
        return self.data_dir / "cache" / "comics"


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings singleton."""
    return Settings()
