"""Integration tests for the Jellyfin compatibility plugin."""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest
from PIL import Image as PilImage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Episode,
    Library,
    LibraryPath,
    MediaFile,
    MediaItem,
    Movie,
    Season,
    Series,
)
from app.db.models import (
    Image as ImageModel,
)
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}
VIEWER = {"username": "viewer", "password": "correct horse"}


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _setup_admin(client: httpx.AsyncClient) -> dict[str, str]:
    res = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert res.status_code == 201, res.text
    return res.json()


async def _create_library(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    name: str,
    lib_type: str = "movie",
) -> tuple[str, Path]:
    root = settings.media_dir / name
    root.mkdir(parents=True, exist_ok=True)
    async with sessionmaker() as session:
        lib = Library(name=f"{name}-{uuid.uuid4().hex[:6]}", type=lib_type)
        lib.paths.append(LibraryPath(path=str(root)))
        session.add(lib)
        await session.commit()
        return lib.id, root


@pytest.mark.asyncio
async def test_jellyfin_plugin_discovery_and_public_endpoints(
    api_client: httpx.AsyncClient,
) -> None:
    admin = await _setup_admin(api_client)
    plugins_res = await api_client.get("/api/v1/plugins", headers=_bearer(admin))
    assert plugins_res.status_code == 200
    ids = {p["id"] for p in plugins_res.json()}
    assert "org.hearth.plugins.jellyfin" in ids

    pub = await api_client.get("/System/Info/Public")
    assert pub.status_code == 200
    data = pub.json()
    assert data["ServerName"] == "Hearth"
    assert data["StartupWizardCompleted"] is True
    assert data["Id"]

    ping = await api_client.get("/System/Ping")
    assert ping.status_code == 200
    assert ping.json() == "Jellyfin Server"

    users_pub = await api_client.get("/Users/Public")
    assert users_pub.status_code == 200
    assert users_pub.json() == []


@pytest.mark.asyncio
async def test_jellyfin_auth_views_browsing_streaming_and_progress(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup_admin(api_client)
    movies_lib_id, movies_root = await _create_library(
        db_settings, db_sessionmaker, "movies", "movie"
    )
    secret_lib_id, _ = await _create_library(db_settings, db_sessionmaker, "secret", "movie")
    tv_lib_id, _ = await _create_library(db_settings, db_sessionmaker, "tv", "tv")

    # Create a non-admin viewer with access only to movies_lib_id and tv_lib_id
    user_res = await api_client.post(
        "/api/v1/users",
        headers=_bearer(admin),
        json={
            "username": VIEWER["username"],
            "password": VIEWER["password"],
            "role": "user",
            "library_ids": [movies_lib_id, tv_lib_id],
        },
    )
    assert user_res.status_code == 201

    # Seed a movie with a real media file + poster image, a secret movie, and a TV show
    video_path = movies_root / "Inception (2010).mp4"
    video_bytes = b"0123456789ABCDEF" * 64
    video_path.write_bytes(video_bytes)

    poster_path = db_settings.images_dir / "poster.png"
    poster_path.parent.mkdir(parents=True, exist_ok=True)
    PilImage.new("RGB", (64, 96), color=(20, 40, 80)).save(poster_path)

    async with db_sessionmaker() as session:
        movie_id = uuid.uuid4().hex
        file_id = uuid.uuid4().hex
        movie_item = MediaItem(
            id=movie_id,
            library_id=movies_lib_id,
            type="movie",
            title="Inception",
            sort_title=sort_title("Inception"),
            year=2010,
            runtime_seconds=100,
            movie=Movie(item_id=movie_id),
            files=[
                MediaFile(
                    id=file_id,
                    item_id=movie_id,
                    path=str(video_path),
                    size_bytes=len(video_bytes),
                    mtime=1.0,
                    partial_hash="hash1",
                    container="mp4",
                    duration_seconds=100.0,
                )
            ],
            images=[
                ImageModel(
                    id=uuid.uuid4().hex,
                    item_id=movie_id,
                    kind="poster",
                    local_path=str(poster_path),
                    width=64,
                    height=96,
                    is_primary=True,
                )
            ],
        )
        secret_item = MediaItem(
            id=uuid.uuid4().hex,
            library_id=secret_lib_id,
            type="movie",
            title="Secret Movie",
            sort_title=sort_title("Secret Movie"),
        )
        series_id = uuid.uuid4().hex
        season_id = uuid.uuid4().hex
        ep_id = uuid.uuid4().hex
        series_item = MediaItem(
            id=series_id,
            library_id=tv_lib_id,
            type="series",
            title="Severance",
            sort_title=sort_title("Severance"),
            series=Series(item_id=series_id),
        )
        season_item = MediaItem(
            id=season_id,
            library_id=tv_lib_id,
            type="season",
            title="Season 1",
            sort_title=sort_title("Season 1"),
            season=Season(item_id=season_id, series_id=series_id, season_number=1),
        )
        ep_item = MediaItem(
            id=ep_id,
            library_id=tv_lib_id,
            type="episode",
            title="Pilot",
            sort_title=sort_title("Pilot"),
            episode=Episode(
                item_id=ep_id,
                series_id=series_id,
                season_id=season_id,
                season_number=1,
                episode_number=1,
            ),
        )
        session.add_all([movie_item, secret_item, series_item, season_item, ep_item])
        await session.commit()

    # Authenticate via Jellyfin endpoint
    auth_hdr = {
        "X-Emby-Authorization": (
            'MediaBrowser Client="Findroid", Device="Pixel", DeviceId="p-1", Version="1.0"'
        )
    }
    bad_login = await api_client.post(
        "/Users/AuthenticateByName",
        headers=auth_hdr,
        json={"Username": VIEWER["username"], "Pw": "wrong"},
    )
    assert bad_login.status_code == 401

    login_res = await api_client.post(
        "/Users/AuthenticateByName",
        headers=auth_hdr,
        json={"Username": VIEWER["username"], "Pw": VIEWER["password"]},
    )
    assert login_res.status_code == 200
    auth_data = login_res.json()
    jf_token = auth_data["AccessToken"]
    jf_user_id = auth_data["User"]["Id"]
    jf_headers = {"X-Emby-Token": jf_token}

    # Views only include granted libraries
    views_res = await api_client.get(f"/Users/{jf_user_id}/Views", headers=jf_headers)
    assert views_res.status_code == 200
    view_ids = {v["Id"] for v in views_res.json()["Items"]}
    assert view_ids == {movies_lib_id, tv_lib_id}
    assert secret_lib_id not in view_ids

    # Items list enforces library access
    items_res = await api_client.get(
        f"/Users/{jf_user_id}/Items",
        headers=jf_headers,
        params={"Recursive": "true"},
    )
    assert items_res.status_code == 200
    item_titles = {it["Name"] for it in items_res.json()["Items"]}
    assert "Inception" in item_titles
    assert "Secret Movie" not in item_titles

    # Direct access to secret item returns 404
    secret_res = await api_client.get(f"/Items/{secret_item.id}", headers=jf_headers)
    assert secret_res.status_code == 404

    # Shows: seasons, episodes, NextUp
    seasons_res = await api_client.get(f"/Shows/{series_id}/Seasons", headers=jf_headers)
    assert seasons_res.status_code == 200
    assert seasons_res.json()["TotalRecordCount"] == 1

    next_up_res = await api_client.get("/Shows/NextUp", headers=jf_headers)
    assert next_up_res.status_code == 200
    assert next_up_res.json()["Items"][0]["Id"] == ep_id

    # PlaybackInfo & Range streaming
    pb_res = await api_client.post(f"/Items/{movie_id}/PlaybackInfo", headers=jf_headers)
    assert pb_res.status_code == 200
    assert len(pb_res.json()["MediaSources"]) == 1

    stream_res = await api_client.get(
        f"/Videos/{movie_id}/stream",
        headers={**jf_headers, "Range": "bytes=0-15"},
    )
    assert stream_res.status_code == 206
    assert stream_res.content == video_bytes[:16]

    # Image endpoint
    img_res = await api_client.get(f"/Items/{movie_id}/Images/Primary", headers=jf_headers)
    assert img_res.status_code == 200
    assert img_res.headers["content-type"] == "image/jpeg"

    # Progress & Favorite reporting
    prog_res = await api_client.post(
        "/Sessions/Playing/Progress",
        headers=jf_headers,
        json={"ItemId": movie_id, "PositionTicks": 500_000_000},
    )
    assert prog_res.status_code == 204

    fav_res = await api_client.post(
        f"/Users/{jf_user_id}/FavoriteItems/{movie_id}",
        headers=jf_headers,
    )
    assert fav_res.status_code == 200
    assert fav_res.json()["IsFavorite"] is True

    detail_res = await api_client.get(f"/Users/{jf_user_id}/Items/{movie_id}", headers=jf_headers)
    assert detail_res.status_code == 200
    assert detail_res.json()["UserData"]["PlaybackPositionTicks"] == 500_000_000
    assert detail_res.json()["UserData"]["IsFavorite"] is True

    # Disabling plugin hides Jellyfin routes immediately
    disable_res = await api_client.post(
        "/api/v1/plugins/org.hearth.plugins.jellyfin/disable",
        headers=_bearer(admin),
    )
    assert disable_res.status_code == 200
    assert (await api_client.get("/System/Info/Public")).status_code == 404

    enable_res = await api_client.post(
        "/api/v1/plugins/org.hearth.plugins.jellyfin/enable",
        headers=_bearer(admin),
    )
    assert enable_res.status_code == 200
    assert (await api_client.get("/System/Info/Public")).status_code == 200
