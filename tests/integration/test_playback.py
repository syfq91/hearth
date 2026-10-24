"""Integration tests for progress, watched, favorite and rating endpoints."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Library,
    LibraryPath,
    MediaFile,
    MediaItem,
    Movie,
    User,
    UserItemState,
)
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}
RUNTIME_SECONDS = 6000


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _login(client: httpx.AsyncClient, username: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"username": username, "password": "correct horse"}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _add_user(
    client: httpx.AsyncClient, admin: dict[str, str], username: str, library_ids: list[str]
) -> dict[str, str]:
    created = await client.post(
        "/api/v1/users",
        json={
            "username": username,
            "password": "correct horse",
            "role": "user",
            "library_ids": library_ids,
        },
        headers=_bearer(admin),
    )
    assert created.status_code == 201, created.text
    return await _login(client, username)


async def _seed(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    chapters: list[dict[str, object]] | None = None,
    runtime_seconds: int = RUNTIME_SECONDS,
) -> tuple[str, str, str]:
    """Create a library, one movie item and its file; return their ids."""
    root = settings.media_dir / f"Movies {uuid.uuid4().hex[:8]}"
    root.mkdir()
    path = root / "The Matrix (1999).mkv"
    path.write_bytes(b"movie")
    stat = path.stat()

    async with sessionmaker() as session:
        library = Library(name=f"Movies {uuid.uuid4().hex[:8]}", type="movie")
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.flush()
        item = MediaItem(
            library_id=library.id,
            type="movie",
            title="The Matrix",
            sort_title=sort_title("The Matrix"),
            year=1999,
            runtime_seconds=runtime_seconds,
            match_state="matched",
        )
        item.movie = Movie()
        session.add(item)
        await session.flush()
        media_file = MediaFile(
            item_id=item.id,
            path=str(path),
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
            partial_hash=uuid.uuid4().hex * 2,
            container="matroska",
            duration_seconds=float(runtime_seconds),
            streams_json={"chapters": chapters or []},
        )
        session.add(media_file)
        await session.commit()
        return library.id, item.id, media_file.id


async def _progress(
    client: httpx.AsyncClient, tokens: dict[str, str], item_id: str, **body: object
) -> httpx.Response:
    return await client.post(
        f"/api/v1/items/{item_id}/progress", json=body, headers=_bearer(tokens)
    )


async def test_progress_is_per_user_and_isolated(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    library_id, item_id, _ = await _seed(db_settings, db_sessionmaker)
    alice = await _add_user(api_client, admin, "alice", [library_id])
    bob = await _add_user(api_client, admin, "bob", [library_id])

    first = await _progress(api_client, alice, item_id, position_ms=1_200_000, state="playing")
    assert first.status_code == 200, first.text
    assert first.json()["position_ms"] == 1_200_000
    assert first.json()["watched"] is False

    second = await _progress(api_client, bob, item_id, position_ms=4_800_000, state="paused")
    assert second.status_code == 200, second.text
    assert second.json()["position_ms"] == 4_800_000

    alice_item = await api_client.get(f"/api/v1/items/{item_id}", headers=_bearer(alice))
    assert alice_item.json()["watch_state"]["position_ms"] == 1_200_000
    bob_item = await api_client.get(f"/api/v1/items/{item_id}", headers=_bearer(bob))
    assert bob_item.json()["watch_state"]["position_ms"] == 4_800_000

    favorited = await api_client.post(
        f"/api/v1/items/{item_id}/favorite", json={"favorite": True}, headers=_bearer(alice)
    )
    assert favorited.status_code == 200, favorited.text
    assert favorited.json()["favorite"] is True
    bob_item = await api_client.get(f"/api/v1/items/{item_id}", headers=_bearer(bob))
    assert bob_item.json()["watch_state"]["favorite"] is False


async def test_threshold_auto_marks_watched(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, _ = await _seed(db_settings, db_sessionmaker)

    below = await _progress(api_client, admin, item_id, position_ms=5_399_999)
    assert below.status_code == 200
    assert below.json()["watched"] is False
    assert below.json()["auto_watched"] is False

    at_threshold = await _progress(api_client, admin, item_id, position_ms=5_400_000)
    assert at_threshold.status_code == 200
    assert at_threshold.json()["watched"] is True
    assert at_threshold.json()["auto_watched"] is True
    assert at_threshold.json()["watched_reason"] == "threshold"

    detail = await api_client.get(f"/api/v1/items/{item_id}", headers=_bearer(admin))
    assert detail.json()["watch_state"]["watched"] is True

    repeated = await _progress(api_client, admin, item_id, position_ms=5_500_000)
    assert repeated.json()["watched"] is True
    assert repeated.json()["auto_watched"] is False


async def test_client_duration_overrides_the_catalog_runtime(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, _ = await _seed(db_settings, db_sessionmaker)

    response = await _progress(api_client, admin, item_id, position_ms=90_000, duration_ms=100_000)
    assert response.status_code == 200
    assert response.json()["watched"] is True
    assert response.json()["watched_reason"] == "threshold"

    response = await _progress(api_client, admin, item_id, position_ms=9000, duration_ms=100_000)
    assert response.json()["watched"] is True


async def test_credits_chapter_marks_watched_below_the_threshold(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, _ = await _seed(
        db_settings,
        db_sessionmaker,
        chapters=[
            {"index": 0, "start_seconds": 0.0, "end_seconds": 60.0, "title": "Opening"},
            {"index": 1, "start_seconds": 3000.0, "end_seconds": 3300.0, "title": "End Credits"},
        ],
    )

    before = await _progress(api_client, admin, item_id, position_ms=2_999_000)
    assert before.json()["watched"] is False

    credits = await _progress(api_client, admin, item_id, position_ms=3_000_000)
    assert credits.status_code == 200
    assert credits.json()["watched"] is True
    assert credits.json()["auto_watched"] is True
    assert credits.json()["watched_reason"] == "credits"


async def test_positions_are_clamped_to_the_duration(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, _ = await _seed(db_settings, db_sessionmaker)

    response = await _progress(api_client, admin, item_id, position_ms=9_999_999)
    assert response.status_code == 200
    assert response.json()["position_ms"] == RUNTIME_SECONDS * 1000


async def test_watched_favorite_and_rating_updates(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, _ = await _seed(db_settings, db_sessionmaker)
    headers = _bearer(admin)
    url = f"/api/v1/items/{item_id}"

    await _progress(api_client, admin, item_id, position_ms=1_000_000)

    watched = await api_client.post(f"{url}/watched", json={"watched": True}, headers=headers)
    assert watched.status_code == 200, watched.text
    assert watched.json()["watched"] is True
    assert watched.json()["position_ms"] == 1_000_000

    unwatched = await api_client.post(f"{url}/watched", json={"watched": False}, headers=headers)
    assert unwatched.status_code == 200
    assert unwatched.json()["watched"] is False
    assert unwatched.json()["position_ms"] == 0

    rated = await api_client.put(f"{url}/rating", json={"rating": 8}, headers=headers)
    assert rated.status_code == 200, rated.text
    assert rated.json()["rating"] == 8
    cleared = await api_client.put(f"{url}/rating", json={"rating": None}, headers=headers)
    assert cleared.status_code == 200
    assert cleared.json()["rating"] is None

    for invalid in (0, 11):
        rejected = await api_client.put(f"{url}/rating", json={"rating": invalid}, headers=headers)
        assert rejected.status_code == 422

    favorited = await api_client.post(f"{url}/favorite", json={"favorite": True}, headers=headers)
    assert favorited.json()["favorite"] is True
    unfavorited = await api_client.post(
        f"{url}/favorite", json={"favorite": False}, headers=headers
    )
    assert unfavorited.json()["favorite"] is False


async def test_play_count_starts_one_session_per_gap(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, _ = await _seed(db_settings, db_sessionmaker)

    first = await _progress(api_client, admin, item_id, position_ms=1000, state="playing")
    assert first.json()["play_count"] == 1
    second = await _progress(api_client, admin, item_id, position_ms=2000, state="playing")
    assert second.json()["play_count"] == 1

    async with db_sessionmaker() as session:
        state = await session.get(UserItemState, ((await _user_id(session, "admin")), item_id))
        assert state is not None
        state.last_played_at = datetime.now(UTC) - timedelta(minutes=10)
        await session.commit()

    resumed = await _progress(api_client, admin, item_id, position_ms=3000, state="playing")
    assert resumed.json()["play_count"] == 2


async def _user_id(session: AsyncSession, username: str) -> str:
    user = await session.scalar(select(User).where(User.username == username))
    assert user is not None
    return user.id


async def test_playback_requires_access_to_the_item(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    library_id, item_id, _ = await _seed(db_settings, db_sessionmaker)
    outsider = await _add_user(api_client, admin, "outsider", [])
    assert library_id

    assert (
        await api_client.post(f"/api/v1/items/{item_id}/progress", json={"position_ms": 1})
    ).status_code == 401
    assert (await _progress(api_client, outsider, item_id, position_ms=1)).status_code == 404
    assert (
        await api_client.post(
            "/api/v1/items/missing/favorite", json={"favorite": True}, headers=_bearer(admin)
        )
    ).status_code == 404
