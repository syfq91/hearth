"""ORM models for Phase 1: accounts, libraries, settings, jobs and audit."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.ids import new_id
from app.db.base import Base, TimestampMixin, UTCDateTime, utcnow


class User(TimestampMixin, Base):
    """A local account. Usernames are stored lowercase and compared exactly."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('admin', 'user')", name="role"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_bitrate: Mapped[int | None] = mapped_column(Integer, default=None)
    allow_transcode: Mapped[bool] = mapped_column(Boolean, default=True)
    max_rating: Mapped[str | None] = mapped_column(String(16), default=None)
    listenbrainz_token: Mapped[str | None] = mapped_column(String(255), default=None, nullable=True)
    # Incremented on password reset or forced logout so old access tokens die.
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    library_access: Mapped[list["LibraryAccess"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    playlists: Mapped[list["Playlist"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    book_progress: Mapped[list["BookProgress"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    """A single refresh token, stored only as a SHA-256 hash.

    Rotation keeps the family id; presenting an already-revoked token revokes
    every token in that family (reuse detection).
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    user: Mapped["User"] = relationship(back_populates="refresh_tokens")


class Library(TimestampMixin, Base):
    """A media library of one type with one or more root paths."""

    __tablename__ = "libraries"
    __table_args__ = (CheckConstraint("type IN ('movie', 'tv', 'music', 'book')", name="type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    type: Mapped[str] = mapped_column(String(16))

    # Scan bookkeeping. Statistics are JSON so the shape can grow without a
    # migration; the status is a short machine-readable state.
    last_scan_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    last_scan_status: Mapped[str | None] = mapped_column(String(16), default=None)
    last_scan_error: Mapped[str | None] = mapped_column(Text, default=None)
    last_scan_stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    paths: Mapped[list["LibraryPath"]] = relationship(
        back_populates="library", cascade="all, delete-orphan", lazy="selectin"
    )
    access: Mapped[list["LibraryAccess"]] = relationship(
        back_populates="library", cascade="all, delete-orphan", lazy="selectin"
    )


class LibraryPath(Base):
    """A validated root path belonging to a library."""

    __tablename__ = "library_paths"
    __table_args__ = (UniqueConstraint("library_id", "path", name="library_path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    library_id: Mapped[str] = mapped_column(
        ForeignKey("libraries.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    library: Mapped["Library"] = relationship(back_populates="paths")


class LibraryAccess(Base):
    """Grant of one library to one user."""

    __tablename__ = "library_access"
    __table_args__ = (UniqueConstraint("library_id", "user_id", name="library_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    library_id: Mapped[str] = mapped_column(
        ForeignKey("libraries.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    library: Mapped["Library"] = relationship(back_populates="access")
    user: Mapped["User"] = relationship(back_populates="library_access")


class Setting(Base):
    """A key/value application setting, JSON-encoded for forward compatibility."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="null")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Job(TimestampMixin, Base):
    """Durable background job with claiming, retry and cancellation state."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="status",
        ),
        Index("ix_jobs_claim", "status", "run_after"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    claimed_by: Mapped[str | None] = mapped_column(String(64), default=None)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)


class AuditLog(Base):
    """Append-only record of administrative actions."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True, default=None
    )
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str | None] = mapped_column(String(32), default=None)
    target_id: Mapped[str | None] = mapped_column(String(36), default=None)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class MediaItem(TimestampMixin, Base):
    """A catalog entry shared by every media type.

    Type-specific tables (``movies`` first) hold only the extra columns. Files,
    images and user state hang off this row, so replacing a file (or matching a
    provider) never loses watch progress that is keyed by item id.
    """

    __tablename__ = "media_items"
    __table_args__ = (
        CheckConstraint(
            "type IN ('movie', 'series', 'season', 'episode', 'artist', 'album', 'track', 'book')",
            name="type",
        ),
        CheckConstraint(
            "match_state IN ('pending', 'matched', 'unmatched')",
            name="match_state",
        ),
        Index("ix_media_items_library_title", "library_id", "sort_title"),
        Index("ix_media_items_library_added", "library_id", "added_at"),
        Index("ix_media_items_match_due", "match_state", "next_match_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    library_id: Mapped[str] = mapped_column(
        ForeignKey("libraries.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(String(16), default="movie", index=True)
    title: Mapped[str] = mapped_column(String(512))
    sort_title: Mapped[str] = mapped_column(String(512), index=True)
    year: Mapped[int | None] = mapped_column(Integer, default=None, index=True)
    overview: Mapped[str | None] = mapped_column(Text, default=None)
    rating: Mapped[float | None] = mapped_column(Float, default=None)
    content_rating: Mapped[str | None] = mapped_column(String(16), default=None, index=True)
    runtime_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    match_state: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    match_attempts: Mapped[int] = mapped_column(Integer, default=0)
    match_error: Mapped[str | None] = mapped_column(Text, default=None)
    next_match_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    matched_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    provider_ids: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    movie: Mapped["Movie | None"] = relationship(
        back_populates="item", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
    series: Mapped["Series | None"] = relationship(
        back_populates="item", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
    season: Mapped["Season | None"] = relationship(
        back_populates="item", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
    episode: Mapped["Episode | None"] = relationship(
        back_populates="item", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
    artist: Mapped["Artist | None"] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
        lazy="selectin",
    )
    album: Mapped["Album | None"] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
        lazy="selectin",
    )
    track: Mapped["Track | None"] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
        lazy="selectin",
    )
    book: Mapped["Book | None"] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
        lazy="selectin",
    )
    book_progress: Mapped[list["BookProgress"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )
    files: Mapped[list["MediaFile"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", lazy="selectin"
    )
    images: Mapped[list["Image"]] = relationship(
        back_populates="item", cascade="all, delete-orphan", lazy="selectin"
    )
    genres: Mapped[list["Genre"]] = relationship(
        secondary="item_genres", lazy="selectin", viewonly=True
    )
    user_states: Mapped[list["UserItemState"]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class Movie(Base):
    """Movie-specific metadata attached to one item."""

    __tablename__ = "movies"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    original_title: Mapped[str | None] = mapped_column(String(512), default=None)
    edition: Mapped[str | None] = mapped_column(String(64), default=None)
    part: Mapped[int | None] = mapped_column(Integer, default=None)
    tagline: Mapped[str | None] = mapped_column(String(512), default=None)
    release_date: Mapped[str | None] = mapped_column(String(10), default=None)

    item: Mapped["MediaItem"] = relationship(back_populates="movie")


class Series(Base):
    """Series-specific metadata attached to one item."""

    __tablename__ = "series"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    original_title: Mapped[str | None] = mapped_column(String(512), default=None)
    status: Mapped[str | None] = mapped_column(String(64), default=None)
    first_air_date: Mapped[str | None] = mapped_column(String(10), default=None)
    network: Mapped[str | None] = mapped_column(String(128), default=None)

    item: Mapped["MediaItem"] = relationship(back_populates="series", lazy="selectin")
    seasons: Mapped[list["Season"]] = relationship(
        back_populates="series", cascade="all, delete-orphan", lazy="selectin"
    )
    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="series", cascade="all, delete-orphan", lazy="selectin"
    )


class Season(Base):
    """Season-specific metadata attached to one item."""

    __tablename__ = "seasons"
    __table_args__ = (
        UniqueConstraint("series_id", "season_number", name="uq_seasons_series_number"),
    )

    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    series_id: Mapped[str] = mapped_column(
        ForeignKey("series.item_id", ondelete="CASCADE"), index=True
    )
    season_number: Mapped[int] = mapped_column(Integer, index=True)
    air_date: Mapped[str | None] = mapped_column(String(10), default=None)

    item: Mapped["MediaItem"] = relationship(back_populates="season", lazy="selectin")
    series: Mapped["Series"] = relationship(back_populates="seasons", lazy="selectin")
    episodes: Mapped[list["Episode"]] = relationship(
        back_populates="season", cascade="all, delete-orphan", lazy="selectin"
    )


class Episode(Base):
    """Episode-specific metadata attached to one item."""

    __tablename__ = "episodes"
    __table_args__ = (
        Index("ix_episodes_series_season_episode", "series_id", "season_number", "episode_number"),
    )

    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    series_id: Mapped[str] = mapped_column(
        ForeignKey("series.item_id", ondelete="CASCADE"), index=True
    )
    season_id: Mapped[str | None] = mapped_column(
        ForeignKey("seasons.item_id", ondelete="CASCADE"), index=True, default=None
    )
    season_number: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    episode_number: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    air_date: Mapped[str | None] = mapped_column(String(10), default=None)
    absolute_number: Mapped[int | None] = mapped_column(Integer, default=None)
    file_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL"), index=True, default=None
    )

    item: Mapped["MediaItem"] = relationship(back_populates="episode", lazy="selectin")
    series: Mapped["Series"] = relationship(back_populates="episodes", lazy="selectin")
    season: Mapped["Season | None"] = relationship(back_populates="episodes", lazy="selectin")
    file: Mapped["MediaFile | None"] = relationship(
        foreign_keys=[file_id], back_populates="episodes", lazy="selectin"
    )


class Artist(Base):
    """Artist-specific metadata attached to one item."""

    __tablename__ = "artists"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    musicbrainz_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    sort_name: Mapped[str | None] = mapped_column(String(512), default=None)
    country: Mapped[str | None] = mapped_column(String(64), default=None)
    disambiguation: Mapped[str | None] = mapped_column(String(255), default=None)

    item: Mapped["MediaItem"] = relationship(back_populates="artist", lazy="joined")
    albums: Mapped[list["Album"]] = relationship(
        back_populates="artist",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    tracks: Mapped[list["Track"]] = relationship(back_populates="artist", lazy="selectin")


class Album(Base):
    """Album-specific metadata attached to one item."""

    __tablename__ = "albums"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    artist_id: Mapped[str | None] = mapped_column(
        ForeignKey("artists.item_id", ondelete="CASCADE"), index=True, default=None
    )
    album_artist: Mapped[str | None] = mapped_column(String(512), default=None)
    musicbrainz_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    musicbrainz_release_group_id: Mapped[str | None] = mapped_column(
        String(36), default=None, index=True
    )
    release_date: Mapped[str | None] = mapped_column(String(10), default=None)
    total_discs: Mapped[int | None] = mapped_column(Integer, default=1)
    total_tracks: Mapped[int | None] = mapped_column(Integer, default=None)

    item: Mapped["MediaItem"] = relationship(back_populates="album", lazy="joined")
    artist: Mapped["Artist | None"] = relationship(back_populates="albums", lazy="joined")
    tracks: Mapped[list["Track"]] = relationship(
        back_populates="album",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class Track(Base):
    """Track-specific metadata attached to one item."""

    __tablename__ = "tracks"
    __table_args__ = (
        Index("ix_tracks_album_disc_track", "album_id", "disc_number", "track_number"),
    )

    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    album_id: Mapped[str | None] = mapped_column(
        ForeignKey("albums.item_id", ondelete="CASCADE"), index=True, default=None
    )
    artist_id: Mapped[str | None] = mapped_column(
        ForeignKey("artists.item_id", ondelete="SET NULL"), index=True, default=None
    )
    file_id: Mapped[str | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL"), index=True, default=None
    )
    disc_number: Mapped[int] = mapped_column(Integer, default=1)
    track_number: Mapped[int | None] = mapped_column(Integer, default=None)
    duration_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    bitrate: Mapped[int | None] = mapped_column(Integer, default=None)
    sample_rate: Mapped[int | None] = mapped_column(Integer, default=None)
    channels: Mapped[int | None] = mapped_column(Integer, default=2)
    replaygain_track_gain: Mapped[float | None] = mapped_column(Float, default=None)
    replaygain_track_peak: Mapped[float | None] = mapped_column(Float, default=None)
    replaygain_album_gain: Mapped[float | None] = mapped_column(Float, default=None)
    replaygain_album_peak: Mapped[float | None] = mapped_column(Float, default=None)
    musicbrainz_track_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    musicbrainz_release_track_id: Mapped[str | None] = mapped_column(String(36), default=None)
    musicbrainz_artist_id: Mapped[str | None] = mapped_column(String(36), default=None)
    musicbrainz_album_id: Mapped[str | None] = mapped_column(String(36), default=None)

    item: Mapped["MediaItem"] = relationship(back_populates="track", lazy="joined")
    album: Mapped["Album | None"] = relationship(back_populates="tracks", lazy="joined")
    artist: Mapped["Artist | None"] = relationship(back_populates="tracks", lazy="joined")
    file: Mapped["MediaFile | None"] = relationship(
        foreign_keys=[file_id], back_populates="tracks", lazy="joined"
    )


class MediaFile(TimestampMixin, Base):
    """One file on disk for a catalog item; multiple files are versions."""

    __tablename__ = "media_files"
    __table_args__ = (
        Index("ix_media_files_identity", "size_bytes", "partial_hash"),
        Index("ix_media_files_item_missing", "item_id", "missing_since"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(Text, unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[float] = mapped_column(Float)
    # SHA-256 over size plus the first and last 64 KiB; stable across renames.
    partial_hash: Mapped[str] = mapped_column(String(64), index=True)
    container: Mapped[str | None] = mapped_column(String(64), default=None)
    duration_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    bit_rate: Mapped[int | None] = mapped_column(BigInteger, default=None)
    streams_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sidecar_subtitles: Mapped[list[str]] = mapped_column(JSON, default=list)
    probe_error: Mapped[str | None] = mapped_column(Text, default=None)
    version_label: Mapped[str | None] = mapped_column(String(64), default=None)
    missing_since: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)

    item: Mapped["MediaItem"] = relationship(back_populates="files")
    episodes: Mapped[list["Episode"]] = relationship(back_populates="file", lazy="selectin")
    tracks: Mapped[list["Track"]] = relationship(back_populates="file", lazy="selectin")


class Image(Base):
    """A cached artwork original associated with an item."""

    __tablename__ = "images"
    __table_args__ = (Index("ix_images_item_kind", "item_id", "kind"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str | None] = mapped_column(String(32), default=None)
    source_url: Mapped[str | None] = mapped_column(Text, default=None)
    local_path: Mapped[str] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    blurhash: Mapped[str | None] = mapped_column(String(128), default=None)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    item: Mapped["MediaItem"] = relationship(back_populates="images")


class Genre(Base):
    """A reusable genre name."""

    __tablename__ = "genres"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)


class ItemGenre(Base):
    """Association between an item and a genre."""

    __tablename__ = "item_genres"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    genre_id: Mapped[str] = mapped_column(
        ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True
    )


class Person(Base):
    """A cast or crew member."""

    __tablename__ = "people"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), index=True)
    tmdb_id: Mapped[str | None] = mapped_column(String(32), unique=True, default=None)


class ItemPerson(Base):
    """Credit of a person on an item, with role and billing order."""

    __tablename__ = "item_people"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    person_id: Mapped[str] = mapped_column(
        ForeignKey("people.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), primary_key=True, default="cast")
    character: Mapped[str | None] = mapped_column(String(255), default=None)
    ordering: Mapped[int] = mapped_column(Integer, default=0)


class ProviderCache(Base):
    """Cached provider response, keyed by provider and request key."""

    __tablename__ = "provider_cache"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    payload: Mapped[Any] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)


class UserItemState(Base):
    """Per-user watch state, favorite, rating and play count for an item.

    Pulled forward from Phase 3 so move detection can prove that renaming a file
    preserves progress rather than recreating the item.
    """

    __tablename__ = "user_item_state"
    __table_args__ = (CheckConstraint("rating IS NULL OR rating BETWEEN 1 AND 10", name="rating"),)

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    played: Mapped[bool] = mapped_column(Boolean, default=False)
    position_ms: Mapped[int] = mapped_column(Integer, default=0)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    rating: Mapped[int | None] = mapped_column(Integer, default=None)
    play_count: Mapped[int] = mapped_column(Integer, default=0)
    last_played_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), default=None)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    item: Mapped["MediaItem"] = relationship(back_populates="user_states")


class Collection(TimestampMixin, Base):
    """Admin-curated or provider-imported collection of media items."""

    __tablename__ = "collections"
    __table_args__ = (Index("ix_collections_name", "name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255))
    overview: Mapped[str | None] = mapped_column(Text, default=None)
    poster_path: Mapped[str | None] = mapped_column(String(512), default=None)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, unique=True, default=None)

    items: Mapped[list["CollectionItem"]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
        order_by="CollectionItem.position",
        lazy="selectin",
    )


class CollectionItem(Base):
    """An item in a collection at an ordering position."""

    __tablename__ = "collection_items"
    __table_args__ = (
        UniqueConstraint(
            "collection_id", "media_item_id", name="uq_collection_items_collection_item"
        ),
        Index("ix_collection_items_collection_id", "collection_id"),
        Index("ix_collection_items_media_item_id", "media_item_id"),
        Index("ix_collection_items_position", "collection_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    collection_id: Mapped[str] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )
    media_item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    collection: Mapped["Collection"] = relationship(back_populates="items", lazy="joined")
    item: Mapped["MediaItem"] = relationship(lazy="joined")


class Playlist(TimestampMixin, Base):
    """User-created playlist of media items (e.g. tracks)."""

    __tablename__ = "playlists"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, default=None)

    user: Mapped["User"] = relationship(back_populates="playlists", lazy="joined")
    items: Mapped[list["PlaylistItem"]] = relationship(
        back_populates="playlist",
        cascade="all, delete-orphan",
        order_by="PlaylistItem.position",
        lazy="selectin",
    )


class PlaylistItem(Base):
    """An item in a playlist at a specific ordering position."""

    __tablename__ = "playlist_items"
    __table_args__ = (
        UniqueConstraint("playlist_id", "position", name="uq_playlist_items_playlist_id_position"),
        Index("ix_playlist_items_position", "playlist_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    playlist_id: Mapped[str] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    playlist: Mapped["Playlist"] = relationship(back_populates="items", lazy="joined")
    item: Mapped["MediaItem"] = relationship(lazy="joined")


class Author(Base):
    """An author or creator of written works."""

    __tablename__ = "authors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), index=True)
    sort_name: Mapped[str | None] = mapped_column(String(255), default=None, index=True)
    openlibrary_id: Mapped[str | None] = mapped_column(String(32), default=None, index=True)
    overview: Mapped[str | None] = mapped_column(Text, default=None)

    books: Mapped[list["Book"]] = relationship(back_populates="author")


class BookSeries(Base):
    """A book series or collection grouping multiple books in order."""

    __tablename__ = "book_series"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)

    books: Mapped[list["Book"]] = relationship(back_populates="series")


class Book(Base):
    """Book-specific metadata attached to one MediaItem."""

    __tablename__ = "books"

    item_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    author_id: Mapped[str | None] = mapped_column(
        ForeignKey("authors.id", ondelete="SET NULL"), index=True, default=None
    )
    series_id: Mapped[str | None] = mapped_column(
        ForeignKey("book_series.id", ondelete="SET NULL"), index=True, default=None
    )
    series_index: Mapped[float | None] = mapped_column(Float, default=None)
    isbn: Mapped[str | None] = mapped_column(String(32), default=None, index=True)
    publisher: Mapped[str | None] = mapped_column(String(255), default=None)
    published_date: Mapped[str | None] = mapped_column(String(32), default=None)
    page_count: Mapped[int | None] = mapped_column(Integer, default=None)
    language: Mapped[str | None] = mapped_column(String(16), default=None)

    item: Mapped["MediaItem"] = relationship(back_populates="book", lazy="joined")
    author: Mapped["Author | None"] = relationship(back_populates="books", lazy="joined")
    series: Mapped["BookSeries | None"] = relationship(back_populates="books", lazy="joined")


class BookProgress(Base):
    """Per-user reading progress for a book."""

    __tablename__ = "book_progress"
    __table_args__ = (
        CheckConstraint("percentage >= 0.0 AND percentage <= 100.0", name="percentage_bounds"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    book_id: Mapped[str] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), primary_key=True
    )
    locator: Mapped[str] = mapped_column(String(512))
    percentage: Mapped[float] = mapped_column(Float, default=0.0)
    device_name: Mapped[str | None] = mapped_column(String(128), default=None)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    user: Mapped["User"] = relationship(back_populates="book_progress", lazy="joined")
    item: Mapped["MediaItem"] = relationship(back_populates="book_progress", lazy="joined")
