"""Integration tests for library CRUD and path validation."""

import os
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import AuditLog, Library

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _create_library(
    client: httpx.AsyncClient, admin: dict[str, str], path: Path, name: str = "Movies"
) -> httpx.Response:
    return await client.post(
        "/api/v1/libraries",
        json={"name": name, "type": "movie", "paths": [str(path)]},
        headers=_bearer(admin),
    )


async def test_admin_creates_library_with_normalized_path(
    api_client: httpx.AsyncClient, db_settings: Settings
) -> None:
    admin = await _setup(api_client)
    root = Path(db_settings.media_dir) / "movies"
    root.mkdir()

    response = await _create_library(api_client, admin, root)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Movies"
    assert body["type"] == "movie"
    assert body["paths"] == [str(root.resolve())]


async def test_library_path_outside_media_mount_is_rejected(
    api_client: httpx.AsyncClient, tmp_path: Path
) -> None:
    admin = await _setup(api_client)
    outside = tmp_path / "outside"
    outside.mkdir()

    response = await _create_library(api_client, admin, outside)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_library_path"


async def test_missing_or_file_paths_are_rejected(
    api_client: httpx.AsyncClient, db_settings: Settings
) -> None:
    admin = await _setup(api_client)
    media = Path(db_settings.media_dir)
    file_path = media / "movie.mkv"
    file_path.write_text("not really a movie", encoding="utf-8")

    missing = await _create_library(api_client, admin, media / "missing", name="Missing")
    not_dir = await _create_library(api_client, admin, file_path, name="File")

    assert missing.status_code == 422
    assert not_dir.status_code == 422
    assert missing.json()["error"]["code"] == "invalid_library_path"


async def test_symlink_escape_is_rejected(
    api_client: httpx.AsyncClient, db_settings: Settings, tmp_path: Path
) -> None:
    admin = await _setup(api_client)
    media = Path(db_settings.media_dir)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = media / "sneaky"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")

    response = await _create_library(api_client, admin, link)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_library_path"


async def test_unreadable_path_is_rejected(
    api_client: httpx.AsyncClient, db_settings: Settings
) -> None:
    if os.geteuid() == 0:
        pytest.skip("root can read every path")
    admin = await _setup(api_client)
    root = Path(db_settings.media_dir) / "locked"
    root.mkdir()
    root.chmod(0o000)
    try:
        response = await _create_library(api_client, admin, root)
    finally:
        root.chmod(0o755)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_library_path"


async def test_non_admin_cannot_create_library(
    api_client: httpx.AsyncClient, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    from app.core.passwords import hash_password
    from app.db.models import User

    await _setup(api_client)
    async with db_sessionmaker() as session:
        session.add(User(username="viewer", password_hash=hash_password("viewerpass")))
        await session.commit()
    login = await api_client.post(
        "/api/v1/auth/login", json={"username": "viewer", "password": "viewerpass"}
    )

    response = await api_client.post(
        "/api/v1/libraries",
        json={"name": "Nope", "type": "movie", "paths": ["/tmp"]},
        headers=_bearer(login.json()),
    )

    assert response.status_code == 403


async def test_duplicate_name_is_rejected(
    api_client: httpx.AsyncClient, db_settings: Settings
) -> None:
    admin = await _setup(api_client)
    root = Path(db_settings.media_dir)
    assert (await _create_library(api_client, admin, root)).status_code == 201

    response = await _create_library(api_client, admin, root, name="Movies")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "library_name_taken"


async def test_update_and_delete_library(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    media = Path(db_settings.media_dir)
    first = media / "first"
    second = media / "second"
    first.mkdir()
    second.mkdir()
    created = (await _create_library(api_client, admin, first)).json()

    updated = await api_client.patch(
        f"/api/v1/libraries/{created['id']}",
        json={"name": "Renamed", "type": "tv", "paths": [str(second)]},
        headers=_bearer(admin),
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"
    assert updated.json()["type"] == "tv"
    assert updated.json()["paths"] == [str(second.resolve())]

    deleted = await api_client.delete(f"/api/v1/libraries/{created['id']}", headers=_bearer(admin))
    assert deleted.status_code == 204
    assert (
        await api_client.get(f"/api/v1/libraries/{created['id']}", headers=_bearer(admin))
    ).status_code == 404

    async with db_sessionmaker() as session:
        assert await session.scalar(select(Library)) is None
        actions = (
            await session.scalars(select(AuditLog.action).order_by(AuditLog.created_at))
        ).all()
    assert {"library.create", "library.update", "library.delete"} <= set(actions)


async def test_normal_user_library_detail_hides_paths(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    from app.core.passwords import hash_password
    from app.db.models import LibraryAccess, User

    admin = await _setup(api_client)
    root = Path(db_settings.media_dir) / "visible"
    root.mkdir()
    created = (await _create_library(api_client, admin, root)).json()

    async with db_sessionmaker() as session:
        user = User(username="viewer", password_hash=hash_password("viewerpass"))
        session.add(user)
        await session.flush()
        session.add(LibraryAccess(library_id=created["id"], user_id=user.id))
        await session.commit()

    login = await api_client.post(
        "/api/v1/auth/login", json={"username": "viewer", "password": "viewerpass"}
    )
    viewer_tokens = login.json()

    viewer_detail = await api_client.get(
        f"/api/v1/libraries/{created['id']}", headers=_bearer(viewer_tokens)
    )
    admin_detail = await api_client.get(
        f"/api/v1/libraries/{created['id']}", headers=_bearer(admin)
    )

    assert viewer_detail.status_code == 200
    assert "paths" not in viewer_detail.json()
    assert admin_detail.json()["paths"] == [str(root.resolve())]
