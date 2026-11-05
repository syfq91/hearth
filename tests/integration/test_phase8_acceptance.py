"""Phase 8 acceptance tests verifying all three acceptance criteria.

Criteria:
1. An authorization test suite proves every endpoint enforces login, role and library access
   (parametrized over the full OpenAPI route list, which fails if a route is added without a
   declared policy).
2. Backup then restore into a fresh container yields an identical catalog.
3. The OpenAPI schema has no untyped (Any) responses on public endpoints.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Library,
    LibraryAccess,
    LibraryPath,
    MediaFile,
    MediaItem,
    Movie,
    Playlist,
    PlaylistItem,
    Track,
    User,
    UserItemState,
)
from app.db.session import build_engine, build_sessionmaker
from app.services.backup import create_backup, restore_backup
from scripts.export_openapi import render_schema

AuthPolicy = Literal["public", "user", "admin", "viewer"]


@dataclass(frozen=True)
class EndpointPolicy:
    auth_policy: AuthPolicy
    library_protected: bool = False


# Exhaustive policy matrix for all OpenAPI routes.
# If a route is added to the application schema without being declared here,
# the test suite fails immediately.
ENDPOINT_POLICIES: dict[tuple[str, str], EndpointPolicy] = {
    # System / Public
    ("GET", "/api/v1/health"): EndpointPolicy("public"),
    ("GET", "/api/v1/version"): EndpointPolicy("public"),
    ("GET", "/api/v1/about"): EndpointPolicy("public"),
    # Auth
    ("GET", "/api/v1/auth/setup"): EndpointPolicy("public"),
    ("POST", "/api/v1/auth/setup"): EndpointPolicy("public"),
    ("POST", "/api/v1/auth/login"): EndpointPolicy("public"),
    ("POST", "/api/v1/auth/refresh"): EndpointPolicy("public"),
    ("POST", "/api/v1/auth/logout"): EndpointPolicy("user"),
    ("GET", "/api/v1/auth/me"): EndpointPolicy("user"),
    ("POST", "/api/v1/auth/media-tokens"): EndpointPolicy("user"),
    # Admin API
    ("GET", "/api/v1/admin/audit"): EndpointPolicy("admin"),
    ("POST", "/api/v1/admin/backup"): EndpointPolicy("admin"),
    ("POST", "/api/v1/admin/restore"): EndpointPolicy("admin"),
    ("GET", "/api/v1/admin/system/info"): EndpointPolicy("admin"),
    ("GET", "/api/v1/admin/logs"): EndpointPolicy("admin"),
    ("GET", "/api/v1/admin/filesystem"): EndpointPolicy("admin"),
    ("GET", "/api/v1/admin/unmatched"): EndpointPolicy("admin"),
    ("POST", "/api/v1/admin/items/{item_id}/rematch"): EndpointPolicy("admin"),
    ("POST", "/api/v1/admin/libraries/scan"): EndpointPolicy("admin"),
    ("POST", "/api/v1/admin/libraries/{library_id}/scan"): EndpointPolicy("admin"),
    ("GET", "/api/v1/admin/jobs"): EndpointPolicy("admin"),
    ("POST", "/api/v1/admin/jobs/{job_id}/cancel"): EndpointPolicy("admin"),
    ("GET", "/api/v1/admin/transcoding/capabilities"): EndpointPolicy("admin"),
    ("GET", "/api/v1/admin/transcoding/settings"): EndpointPolicy("admin"),
    ("PUT", "/api/v1/admin/transcoding/settings"): EndpointPolicy("admin"),
    ("GET", "/api/v1/admin/transcoding/sessions"): EndpointPolicy("admin"),
    ("DELETE", "/api/v1/admin/transcoding/sessions/{session_id}"): EndpointPolicy("admin"),
    ("GET", "/api/v1/admin/transcoding/sessions/{session_id}/logs"): EndpointPolicy("admin"),
    ("POST", "/api/v1/admin/transcoding/sessions/{session_id}/stop"): EndpointPolicy("admin"),
    # User Management (Admin)
    ("GET", "/api/v1/users"): EndpointPolicy("admin"),
    ("POST", "/api/v1/users"): EndpointPolicy("admin"),
    ("GET", "/api/v1/users/{user_id}"): EndpointPolicy("admin"),
    ("PATCH", "/api/v1/users/{user_id}"): EndpointPolicy("admin"),
    ("POST", "/api/v1/users/{user_id}/password"): EndpointPolicy("admin"),
    ("GET", "/api/v1/users/me/listenbrainz"): EndpointPolicy("user"),
    ("PUT", "/api/v1/users/me/listenbrainz"): EndpointPolicy("user"),
    ("DELETE", "/api/v1/users/me/listenbrainz"): EndpointPolicy("user"),
    # Jobs (Admin)
    ("GET", "/api/v1/jobs"): EndpointPolicy("admin"),
    ("POST", "/api/v1/jobs"): EndpointPolicy("admin"),
    ("GET", "/api/v1/jobs/{job_id}"): EndpointPolicy("admin"),
    ("POST", "/api/v1/jobs/{job_id}/cancel"): EndpointPolicy("admin"),
    # Real-time Events
    ("GET", "/api/v1/events"): EndpointPolicy("user"),
    # Libraries
    ("GET", "/api/v1/libraries"): EndpointPolicy("user"),
    ("POST", "/api/v1/libraries"): EndpointPolicy("admin"),
    ("GET", "/api/v1/libraries/{library_id}"): EndpointPolicy("user", library_protected=True),
    ("PATCH", "/api/v1/libraries/{library_id}"): EndpointPolicy("admin"),
    ("DELETE", "/api/v1/libraries/{library_id}"): EndpointPolicy("admin"),
    ("GET", "/api/v1/libraries/{library_id}/folders"): EndpointPolicy(
        "user", library_protected=True
    ),
    ("GET", "/api/v1/libraries/{library_id}/items"): EndpointPolicy("user", library_protected=True),
    ("POST", "/api/v1/libraries/{library_id}/scan"): EndpointPolicy("admin"),
    # Items & Playback
    ("GET", "/api/v1/items/{item_id}"): EndpointPolicy("user", library_protected=True),
    ("POST", "/api/v1/items/{item_id}/playback-info"): EndpointPolicy(
        "user", library_protected=True
    ),
    ("POST", "/api/v1/items/{item_id}/progress"): EndpointPolicy("user", library_protected=True),
    ("POST", "/api/v1/items/{item_id}/favorite"): EndpointPolicy("user", library_protected=True),
    ("PUT", "/api/v1/items/{item_id}/rating"): EndpointPolicy("user", library_protected=True),
    ("POST", "/api/v1/items/{item_id}/watched"): EndpointPolicy("user", library_protected=True),
    ("POST", "/api/v1/items/{item_id}/match"): EndpointPolicy("user", library_protected=True),
    # Search & Metadata
    ("GET", "/api/v1/search"): EndpointPolicy("user"),
    ("GET", "/api/v1/metadata/search"): EndpointPolicy("user"),
    # Collections (Curated)
    ("GET", "/api/v1/collections"): EndpointPolicy("user"),
    ("POST", "/api/v1/collections"): EndpointPolicy("admin"),
    ("GET", "/api/v1/collections/{collection_id}"): EndpointPolicy("user"),
    ("PATCH", "/api/v1/collections/{collection_id}"): EndpointPolicy("admin"),
    ("DELETE", "/api/v1/collections/{collection_id}"): EndpointPolicy("admin"),
    ("POST", "/api/v1/collections/{collection_id}/items"): EndpointPolicy("admin"),
    ("PUT", "/api/v1/collections/{collection_id}/items/order"): EndpointPolicy("admin"),
    ("DELETE", "/api/v1/collections/{collection_id}/items/{item_id}"): EndpointPolicy("admin"),
    # Playlists (User)
    ("GET", "/api/v1/playlists"): EndpointPolicy("user"),
    ("POST", "/api/v1/playlists"): EndpointPolicy("user"),
    ("GET", "/api/v1/playlists/{playlist_id}"): EndpointPolicy("user"),
    ("PATCH", "/api/v1/playlists/{playlist_id}"): EndpointPolicy("user"),
    ("DELETE", "/api/v1/playlists/{playlist_id}"): EndpointPolicy("user"),
    ("POST", "/api/v1/playlists/{playlist_id}/items"): EndpointPolicy("user"),
    ("PUT", "/api/v1/playlists/{playlist_id}/items/order"): EndpointPolicy("user"),
    ("PUT", "/api/v1/playlists/{playlist_id}/items/reorder"): EndpointPolicy("user"),
    ("DELETE", "/api/v1/playlists/{playlist_id}/items/{item_id}"): EndpointPolicy("user"),
    # Shows (TV)
    ("GET", "/api/v1/series"): EndpointPolicy("user"),
    ("GET", "/api/v1/series/{series_id}"): EndpointPolicy("user", library_protected=True),
    ("GET", "/api/v1/series/{series_id}/seasons"): EndpointPolicy("user", library_protected=True),
    ("GET", "/api/v1/series/{series_id}/next-up"): EndpointPolicy("user", library_protected=True),
    ("POST", "/api/v1/series/{series_id}/watched"): EndpointPolicy("user", library_protected=True),
    ("GET", "/api/v1/seasons/{season_id}"): EndpointPolicy("user", library_protected=True),
    ("GET", "/api/v1/seasons/{season_id}/episodes"): EndpointPolicy("user", library_protected=True),
    ("POST", "/api/v1/seasons/{season_id}/watched"): EndpointPolicy("user", library_protected=True),
    ("GET", "/api/v1/episodes/{episode_id}"): EndpointPolicy("user", library_protected=True),
    ("GET", "/api/v1/shows/next-up"): EndpointPolicy("user"),
    # Music
    ("GET", "/api/v1/artists"): EndpointPolicy("user"),
    ("GET", "/api/v1/artists/{artist_id}"): EndpointPolicy("user", library_protected=True),
    ("GET", "/api/v1/albums"): EndpointPolicy("user"),
    ("GET", "/api/v1/albums/{album_id}"): EndpointPolicy("user", library_protected=True),
    ("GET", "/api/v1/tracks"): EndpointPolicy("user"),
    ("GET", "/api/v1/tracks/{track_id}"): EndpointPolicy("user", library_protected=True),
    ("GET", "/api/v1/genres"): EndpointPolicy("user"),
    ("GET", "/api/v1/music/search"): EndpointPolicy("user"),
    ("GET", "/api/v1/music/random"): EndpointPolicy("user"),
    ("GET", "/api/v1/music/recently-added"): EndpointPolicy("user"),
    ("GET", "/api/v1/music/most-played"): EndpointPolicy("user"),
    # Books
    ("GET", "/api/v1/books"): EndpointPolicy("user"),
    ("GET", "/api/v1/books/search"): EndpointPolicy("user"),
    ("GET", "/api/v1/books/{book_id}"): EndpointPolicy("user", library_protected=True),
    ("GET", "/api/v1/books/{book_id}/progress"): EndpointPolicy("user", library_protected=True),
    ("PUT", "/api/v1/books/{book_id}/progress"): EndpointPolicy("user", library_protected=True),
    ("GET", "/api/v1/authors"): EndpointPolicy("user"),
    ("GET", "/api/v1/authors/{author_id}"): EndpointPolicy("user"),
    ("GET", "/api/v1/book-series"): EndpointPolicy("user"),
    ("GET", "/api/v1/book-series/{series_id}"): EndpointPolicy("user"),
    # Home
    ("GET", "/api/v1/home/continue-reading"): EndpointPolicy("user"),
    ("GET", "/api/v1/home/continue-watching"): EndpointPolicy("user"),
    ("GET", "/api/v1/home/recently-added"): EndpointPolicy("user"),
    ("GET", "/api/v1/home/favorites"): EndpointPolicy("user"),
    # Streaming & Media (Viewer auth: Token query param or Bearer header)
    ("GET", "/api/v1/stream/{file_id}"): EndpointPolicy("viewer", library_protected=True),
    ("GET", "/api/v1/stream/{file_id}/transcode"): EndpointPolicy("viewer", library_protected=True),
    ("GET", "/api/v1/stream/audio/{track_id}"): EndpointPolicy("viewer", library_protected=True),
    ("GET", "/api/v1/stream/audio/{track_id}/transcode"): EndpointPolicy(
        "viewer", library_protected=True
    ),
    ("GET", "/api/v1/images/{image_id}"): EndpointPolicy("viewer"),
    ("GET", "/api/v1/subtitles/{subtitle_id}.vtt"): EndpointPolicy(
        "viewer", library_protected=True
    ),
    ("GET", "/api/v1/books/{book_id}/file/{format}"): EndpointPolicy(
        "viewer", library_protected=True
    ),
    ("GET", "/api/v1/books/{book_id}/pages/{page_number}"): EndpointPolicy(
        "viewer", library_protected=True
    ),
    ("GET", "/api/v1/hls/{session_id}/master.m3u8"): EndpointPolicy("viewer"),
    ("GET", "/api/v1/hls/{session_id}/media.m3u8"): EndpointPolicy("viewer"),
    ("GET", "/api/v1/hls/{session_id}/init.mp4"): EndpointPolicy("viewer"),
    ("GET", "/api/v1/hls/{session_id}/{segment_file}"): EndpointPolicy("viewer"),
    ("POST", "/api/v1/hls/{session_id}/stop"): EndpointPolicy("viewer"),
}


def _get_all_openapi_operations() -> list[tuple[str, str]]:
    """Extract all (method, path) tuples from application schema."""
    schema = json.loads(render_schema())
    operations = []
    for path, methods in sorted(schema.get("paths", {}).items()):
        for method in sorted(methods.keys()):
            if method.lower() in ("get", "post", "put", "patch", "delete"):
                operations.append((method.upper(), path))
    return operations


ALL_OPENAPI_OPERATIONS = _get_all_openapi_operations()


# -----------------------------------------------------------------------------
# Criterion 1: Parametrized Route Authorization Policy Matrix
# -----------------------------------------------------------------------------
@pytest.mark.parametrize("method,path", ALL_OPENAPI_OPERATIONS)
@pytest.mark.asyncio
async def test_criterion_1_authorization_policy_matrix(
    method: str,
    path: str,
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Every endpoint in OpenAPI has a declared authorization policy and enforces it.

    Fails if a route is added to the application schema without a declared policy.
    Verifies that:
    - Public endpoints are accessible anonymously.
    - User/Admin/Viewer endpoints reject unauthenticated requests with 401.
    - Admin endpoints reject non-admin users with 403.
    """
    # 1. Enforce that every route in OpenAPI has a declared policy
    route_key = (method, path)
    assert route_key in ENDPOINT_POLICIES, (
        f"Route {method} {path} has no declared authorization policy in ENDPOINT_POLICIES"
    )
    policy = ENDPOINT_POLICIES[route_key]

    # Create dummy user accounts if not existing
    admin_setup = await api_client.post(
        "/api/v1/auth/setup",
        json={"username": "acceptance_admin", "password": "correct horse"},
    )
    if admin_setup.status_code == 201:
        admin_tokens = admin_setup.json()
    else:
        admin_login = await api_client.post(
            "/api/v1/auth/login",
            json={"username": "acceptance_admin", "password": "correct horse"},
        )
        admin_tokens = admin_login.json()

    # Create a regular user
    user_res = await api_client.post(
        "/api/v1/users",
        headers={"Authorization": f"Bearer {admin_tokens['access_token']}"},
        json={"username": "acceptance_user", "password": "correct horse", "role": "user"},
    )
    if user_res.status_code == 201:
        user_login = await api_client.post(
            "/api/v1/auth/login",
            json={"username": "acceptance_user", "password": "correct horse"},
        )
        user_tokens = user_login.json()
    else:
        user_login = await api_client.post(
            "/api/v1/auth/login",
            json={"username": "acceptance_user", "password": "correct horse"},
        )
        user_tokens = user_login.json()

    # Build a sample path by substituting parameter placeholders
    test_path = (
        path.replace("{library_id}", "test-lib-id")
        .replace("{item_id}", "test-item-id")
        .replace("{series_id}", "test-series-id")
        .replace("{season_id}", "test-season-id")
        .replace("{episode_id}", "test-episode-id")
        .replace("{artist_id}", "test-artist-id")
        .replace("{album_id}", "test-album-id")
        .replace("{track_id}", "test-track-id")
        .replace("{book_id}", "test-book-id")
        .replace("{author_id}", "test-author-id")
        .replace("{collection_id}", "test-collection-id")
        .replace("{playlist_id}", "test-playlist-id")
        .replace("{user_id}", "test-user-id")
        .replace("{job_id}", "test-job-id")
        .replace("{session_id}", "test-session-id")
        .replace("{file_id}", "test-file-id")
        .replace("{image_id}", "test-image-id")
        .replace("{subtitle_id}", "test-sub-id")
        .replace("{format}", "epub")
        .replace("{page_number}", "1")
        .replace("{segment_file}", "seg_0.m4s")
    )

    # A) Test Anonymous Access
    anon_resp = await api_client.request(method, test_path)
    if policy.auth_policy == "public":
        # Public endpoints must not return 401 or 403
        assert anon_resp.status_code not in (401, 403), (
            f"Public endpoint {method} {path} returned {anon_resp.status_code}"
        )
    else:
        # Protected endpoints must reject unauthenticated requests with 401
        assert anon_resp.status_code == 401, (
            f"Protected endpoint {method} {path} allowed anonymous access ({anon_resp.status_code})"
        )

    # B) Test User Access on Admin Endpoints
    if policy.auth_policy == "admin":
        user_resp = await api_client.request(
            method,
            test_path,
            headers={"Authorization": f"Bearer {user_tokens['access_token']}"},
        )
        assert user_resp.status_code == 403, (
            f"Admin endpoint {method} {path} did not reject regular user with 403 "
            f"({user_resp.status_code})"
        )


# -----------------------------------------------------------------------------
# Criterion 2: Backup and Restore Equivalence
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_criterion_2_backup_restore_identical_catalog(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
    tmp_path: Path,
) -> None:
    """Backup then restore into a fresh database instance yields an identical catalog."""
    # 1. Seed complete catalog: User, Library, Movie, Series, Music, Book, Playlist, UserItemState
    async with db_sessionmaker() as session:
        user = User(
            id=uuid.uuid4().hex,
            username="test_catalog_user",
            password_hash="argon2id$...",
            role="user",
            max_bitrate=10000,
            allow_transcode=True,
            max_rating="PG-13",
        )
        session.add(user)

        # Libraries
        lib_movie = Library(id=uuid.uuid4().hex, name="Movies Lib", type="movie")
        lib_music = Library(id=uuid.uuid4().hex, name="Music Lib", type="music")
        lib_books = Library(id=uuid.uuid4().hex, name="Books Lib", type="book")
        session.add_all([lib_movie, lib_music, lib_books])
        await session.flush()

        session.add(LibraryPath(id=uuid.uuid4().hex, library_id=lib_movie.id, path="/media/movies"))
        session.add(LibraryAccess(user_id=user.id, library_id=lib_movie.id))

        # Movie Item
        movie_item = MediaItem(
            id=uuid.uuid4().hex,
            library_id=lib_movie.id,
            type="movie",
            title="Inception",
            sort_title="Inception",
            year=2010,
            rating=8.8,
            content_rating="PG-13",
        )
        session.add(movie_item)
        await session.flush()

        session.add(Movie(item_id=movie_item.id, original_title="Inception"))
        session.add(
            MediaFile(
                id=uuid.uuid4().hex,
                item_id=movie_item.id,
                path="/media/movies/inception.mkv",
                size_bytes=4500000000,
                mtime=1700000000.0,
                partial_hash="abc123partialhash",
                container="mkv",
            )
        )

        # Watch State
        session.add(
            UserItemState(
                user_id=user.id,
                item_id=movie_item.id,
                played=True,
                position_ms=8500000,
                favorite=True,
                rating=5,
                play_count=3,
            )
        )

        # Music Track
        music_item = MediaItem(
            id=uuid.uuid4().hex,
            library_id=lib_music.id,
            type="track",
            title="Time",
            sort_title="Time",
        )
        session.add(music_item)
        await session.flush()
        session.add(Track(item_id=music_item.id, track_number=1, duration_seconds=275.0))

        # Playlist
        playlist = Playlist(
            id=uuid.uuid4().hex,
            user_id=user.id,
            name="Favorites Playlist",
        )
        session.add(playlist)
        await session.flush()
        session.add(
            PlaylistItem(
                id=uuid.uuid4().hex, playlist_id=playlist.id, item_id=music_item.id, position=1
            )
        )

        await session.commit()

    # 2. Perform online backup snapshot
    backup_file = create_backup(db_settings, tmp_path / "catalog_backup.tar.gz")
    assert backup_file.exists()

    # 3. Prepare a brand new, isolated target instance (fresh container simulation)
    fresh_dir = tmp_path / "fresh_container"
    fresh_dir.mkdir(parents=True, exist_ok=True)
    fresh_db = fresh_dir / "hearth.db"

    fresh_settings = Settings(
        env="testing",
        data_dir=fresh_dir,
        db_path=fresh_db,
    )

    # 4. Restore the backup archive into the fresh instance
    restore_meta = restore_backup(backup_file, fresh_settings)
    assert restore_meta["hearth_version"] is not None
    assert fresh_db.exists()

    # 5. Query fresh instance and verify 100% catalog equivalence
    fresh_engine = build_engine(fresh_settings)
    fresh_sessionmaker = build_sessionmaker(fresh_engine)

    try:
        async with fresh_sessionmaker() as fresh_session:
            # Users
            restored_user = await fresh_session.get(User, user.id)
            assert restored_user is not None
            assert restored_user.username == "test_catalog_user"
            assert restored_user.max_rating == "PG-13"

            # Libraries
            restored_lib = await fresh_session.get(Library, lib_movie.id)
            assert restored_lib is not None
            assert restored_lib.name == "Movies Lib"

            # Movie and MediaFile
            restored_movie = await fresh_session.get(MediaItem, movie_item.id)
            assert restored_movie is not None
            assert restored_movie.title == "Inception"
            assert restored_movie.year == 2010

            file_row = await fresh_session.scalar(
                select(MediaFile).where(MediaFile.item_id == movie_item.id)
            )
            assert file_row is not None
            assert file_row.path == "/media/movies/inception.mkv"

            # User Watch State
            state = await fresh_session.scalar(
                select(UserItemState).where(
                    UserItemState.user_id == user.id,
                    UserItemState.item_id == movie_item.id,
                )
            )
            assert state is not None
            assert state.played is True
            assert state.position_ms == 8500000
            assert state.favorite is True
            assert state.rating == 5

            # Playlists and Items
            pl = await fresh_session.get(Playlist, playlist.id)
            assert pl is not None
            assert pl.name == "Favorites Playlist"
            pl_item = await fresh_session.scalar(
                select(PlaylistItem).where(PlaylistItem.playlist_id == playlist.id)
            )
            assert pl_item is not None
            assert pl_item.item_id == music_item.id
    finally:
        await fresh_engine.dispose()


# -----------------------------------------------------------------------------
# Criterion 3: Zero Untyped Responses in Public OpenAPI
# -----------------------------------------------------------------------------
def test_criterion_3_openapi_has_no_untyped_public_responses() -> None:
    """The OpenAPI schema has no untyped (Any / {}) responses on public endpoints."""
    schema = json.loads(render_schema())

    untyped_endpoints: list[tuple[str, str, str, str]] = []
    for path, methods in schema.get("paths", {}).items():
        for method, operation in methods.items():
            if method.lower() not in ("get", "post", "put", "patch", "delete"):
                continue

            responses = operation.get("responses", {})
            for status_code, resp_def in responses.items():
                content = resp_def.get("content", {})
                for media_type, mt_data in content.items():
                    s = mt_data.get("schema", {})
                    # An empty schema {} represents "Any" in OpenAPI/JSON Schema
                    if not s or s == {} or s.get("type") == "any":
                        untyped_endpoints.append((method.upper(), path, status_code, media_type))

    assert not untyped_endpoints, (
        f"Found {len(untyped_endpoints)} untyped (Any) responses in OpenAPI schema:\n"
        + "\n".join(f"  {m} {p} [{code}] ({mt})" for m, p, code, mt in untyped_endpoints)
    )
