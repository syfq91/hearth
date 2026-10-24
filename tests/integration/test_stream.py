"""Integration tests for direct-play streaming and HTTP range support."""

import asyncio
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Library, LibraryPath, MediaFile, MediaItem, Movie
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}
VIEWER = {"username": "viewer", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _seed(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    content: bytes,
    name: str = "The Matrix (1999).mkv",
    library_name: str = "Movies",
    missing: bool = False,
) -> tuple[str, str, str, Path]:
    root = settings.media_dir / library_name
    root.mkdir(exist_ok=True)
    path = root / name
    path.write_bytes(content)
    stat = path.stat()

    async with sessionmaker() as session:
        library = Library(name=f"{library_name} {uuid.uuid4().hex[:8]}", type="movie")
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.flush()
        item = MediaItem(
            library_id=library.id,
            type="movie",
            title="The Matrix",
            sort_title=sort_title("The Matrix"),
            year=1999,
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
            duration_seconds=8160.0,
            missing_since=None,
        )
        session.add(media_file)
        await session.commit()
        return library.id, item.id, media_file.id, path


async def test_stream_supports_full_and_range_requests(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    content = bytes(range(256)) * 8
    _, _, file_id, _ = await _seed(db_settings, db_sessionmaker, content=content)
    url = f"/api/v1/stream/{file_id}"
    headers = _bearer(admin)

    full = await api_client.get(url, headers=headers)
    assert full.status_code == 200
    assert full.content == content
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-type"].startswith("video/x-matroska")
    assert full.headers["content-length"] == str(len(content))
    assert full.headers["etag"].startswith('"')
    assert full.headers["last-modified"].endswith("GMT")

    first = await api_client.get(url, headers={**headers, "Range": "bytes=0-99"})
    assert first.status_code == 206
    assert first.content == content[:100]
    assert first.headers["content-range"] == f"bytes 0-99/{len(content)}"
    assert first.headers["content-length"] == "100"

    open_ended = await api_client.get(url, headers={**headers, "Range": "bytes=100-"})
    assert open_ended.status_code == 206
    assert open_ended.content == content[100:]
    assert open_ended.headers["content-range"] == f"bytes 100-{len(content) - 1}/{len(content)}"

    suffix = await api_client.get(url, headers={**headers, "Range": "bytes=-50"})
    assert suffix.status_code == 206
    assert suffix.content == content[-50:]
    assert (
        suffix.headers["content-range"]
        == f"bytes {len(content) - 50}-{len(content) - 1}/{len(content)}"
    )

    single = await api_client.get(url, headers={**headers, "Range": "bytes=7-7"})
    assert single.status_code == 206
    assert single.content == content[7:8]

    beyond = await api_client.get(url, headers={**headers, "Range": "bytes=99999-"})
    assert beyond.status_code == 416
    assert beyond.headers["content-range"] == f"bytes */{len(content)}"

    multipart = await api_client.get(url, headers={**headers, "Range": "bytes=0-1,4-5"})
    assert multipart.status_code == 200
    assert multipart.content == content

    head = await api_client.head(url, headers=headers)
    assert head.status_code == 200
    assert head.headers["content-length"] == str(len(content))
    assert head.content == b""

    cached = await api_client.get(url, headers={**headers, "If-None-Match": full.headers["etag"]})
    assert cached.status_code == 304

    stale = await api_client.get(
        url, headers={**headers, "If-Range": '"different"', "Range": "bytes=0-9"}
    )
    assert stale.status_code == 200
    assert stale.content == content


async def test_stream_enforces_auth_item_scope_and_library_access(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    content = b"payload" * 100
    library_id, item_id, file_id, _ = await _seed(db_settings, db_sessionmaker, content=content)
    _, other_item_id, _, _ = await _seed(
        db_settings, db_sessionmaker, content=b"other", name="Heat (1995).mkv"
    )
    url = f"/api/v1/stream/{file_id}"

    assert (await api_client.get(url)).status_code == 401

    minted = await api_client.post(
        "/api/v1/auth/media-tokens", json={"item_id": item_id}, headers=_bearer(admin)
    )
    assert minted.status_code == 201
    token = minted.json()["token"]
    with_token = await api_client.get(url, params={"token": token})
    assert with_token.status_code == 200
    assert with_token.content == content

    other = await api_client.post(
        "/api/v1/auth/media-tokens", json={"item_id": other_item_id}, headers=_bearer(admin)
    )
    assert (await api_client.get(url, params={"token": other.json()["token"]})).status_code == 404

    created = await api_client.post(
        "/api/v1/users",
        json={**VIEWER, "role": "user", "library_ids": []},
        headers=_bearer(admin),
    )
    assert created.status_code == 201, created.text
    viewer = await api_client.post(
        "/api/v1/auth/login",
        json={"username": VIEWER["username"], "password": VIEWER["password"]},
    )
    assert (await api_client.get(url, headers=_bearer(viewer.json()))).status_code == 404

    granted = await api_client.post(
        "/api/v1/users",
        json={
            "username": "granted",
            "password": "correct horse",
            "role": "user",
            "library_ids": [library_id],
        },
        headers=_bearer(admin),
    )
    assert granted.status_code == 201, granted.text
    granted_login = await api_client.post(
        "/api/v1/auth/login", json={"username": "granted", "password": "correct horse"}
    )
    assert (await api_client.get(url, headers=_bearer(granted_login.json()))).status_code == 200

    assert (
        await api_client.get("/api/v1/stream/does-not-exist", headers=_bearer(admin))
    ).status_code == 404


async def test_stream_refuses_unregistered_paths_and_missing_files(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    admin = await _setup(api_client)
    _, _, file_id, path = await _seed(db_settings, db_sessionmaker, content=b"movie")
    url = f"/api/v1/stream/{file_id}"

    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"secret")
    async with db_sessionmaker() as session:
        row = await session.get(MediaFile, file_id)
        assert row is not None
        row.path = str(outside)
        await session.commit()
    assert (await api_client.get(url, headers=_bearer(admin))).status_code == 404

    async with db_sessionmaker() as session:
        row = await session.get(MediaFile, file_id)
        assert row is not None
        row.path = str(path)
        row.missing_since = row.created_at
        await session.commit()
    assert (await api_client.get(url, headers=_bearer(admin))).status_code == 404


async def test_five_concurrent_streams_do_not_block_api_responses(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    content = bytes(range(256)) * 4096  # 1 MiB
    _, _, file_id, _ = await _seed(db_settings, db_sessionmaker, content=content)
    url = f"/api/v1/stream/{file_id}"
    headers = _bearer(admin)

    release = asyncio.Event()
    started = asyncio.Event()
    opened = 0

    async def slow_stream() -> bytes:
        nonlocal opened
        async with api_client.stream("GET", url, headers=headers) as response:
            assert response.status_code == 200
            chunks = response.aiter_bytes()
            first = await chunks.__anext__()
            opened += 1
            if opened == 5:
                started.set()
            await release.wait()
            rest = [chunk async for chunk in chunks]
        return first + b"".join(rest)

    streams = [asyncio.create_task(slow_stream()) for _ in range(5)]
    await asyncio.wait_for(started.wait(), timeout=5)

    latencies: list[float] = []
    for _ in range(20):
        begin = time.perf_counter()
        response = await api_client.get("/api/v1/health")
        latencies.append(time.perf_counter() - begin)
        assert response.status_code == 200

    assert max(latencies) < 0.5, f"API was blocked by streaming: {max(latencies):.3f}s"
    release.set()
    bodies = await asyncio.gather(*streams)
    assert all(body == content for body in bodies)
