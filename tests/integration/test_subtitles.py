"""Integration tests for sidecar subtitle listing and WebVTT serving."""

import uuid
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Library, LibraryPath, MediaFile, MediaItem, Movie
from app.services.media.titles import sort_title
from app.services.subtitles import encode_subtitle_id

ADMIN = {"username": "admin", "password": "correct horse"}

VIDEO_NAME = "The Matrix (1999).mkv"
SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello from SRT\n"
ASS = "[Events]\nFormat: Start, End, Text\nDialogue: 0:00:03.00,0:00:04.00,Hello from ASS\n"


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
    sidecars: dict[str, str] | None = None,
    title: str = "The Matrix",
) -> tuple[str, str, str, Path]:
    root = settings.media_dir / f"Movies {uuid.uuid4().hex[:8]}"
    root.mkdir()
    video = root / VIDEO_NAME
    video.write_bytes(b"video")
    for name, content in (sidecars or {}).items():
        (root / name).write_text(content, encoding="utf-8")
    stat = video.stat()

    async with sessionmaker() as session:
        library = Library(name=f"Movies {uuid.uuid4().hex[:8]}", type="movie")
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.flush()
        item = MediaItem(
            library_id=library.id,
            type="movie",
            title=title,
            sort_title=sort_title(title),
            year=1999,
            match_state="matched",
        )
        item.movie = Movie()
        session.add(item)
        await session.flush()
        media_file = MediaFile(
            item_id=item.id,
            path=str(video),
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
            partial_hash=uuid.uuid4().hex * 2,
            container="matroska",
            duration_seconds=8160.0,
        )
        session.add(media_file)
        await session.commit()
        return library.id, item.id, media_file.id, video


async def test_item_detail_lists_sidecars_and_serves_webvtt(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, _, _ = await _seed(
        db_settings,
        db_sessionmaker,
        sidecars={
            "The Matrix (1999).srt": SRT,
            "The Matrix (1999).en.ass": ASS,
            "The Matrix (1999).sub": "not a supported sidecar",
        },
    )

    detail = await api_client.get(f"/api/v1/items/{item_id}", headers=_bearer(admin))
    assert detail.status_code == 200, detail.text
    subtitles = detail.json()["files"][0]["subtitles"]
    assert [(track["format"], track["language"]) for track in subtitles] == [
        ("ass", "en"),
        ("srt", None),
    ]

    srt_track = next(track for track in subtitles if track["format"] == "srt")
    served = await api_client.get(srt_track["url"], headers=_bearer(admin))
    assert served.status_code == 200, served.text
    assert served.headers["content-type"].startswith("text/vtt")
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.text.startswith("WEBVTT\n\n")
    assert "00:00:01.000 --> 00:00:02.000" in served.text
    assert "Hello from SRT" in served.text

    ass_track = next(track for track in subtitles if track["format"] == "ass")
    served_ass = await api_client.get(ass_track["url"], headers=_bearer(admin))
    assert served_ass.status_code == 200
    assert "00:00:03.000 --> 00:00:04.000" in served_ass.text
    assert "Hello from ASS" in served_ass.text


async def test_subtitles_accept_scoped_media_tokens(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, file_id, _ = await _seed(
        db_settings, db_sessionmaker, sidecars={"The Matrix (1999).srt": SRT}
    )
    _, other_item_id, _, _ = await _seed(
        db_settings,
        db_sessionmaker,
        sidecars={"The Matrix (1999).srt": SRT},
        title="Heat",
    )
    url = f"/api/v1/subtitles/{encode_subtitle_id(file_id, 'The Matrix (1999).srt')}.vtt"

    assert (await api_client.get(url)).status_code == 401

    minted = await api_client.post(
        "/api/v1/auth/media-tokens", json={"item_id": item_id}, headers=_bearer(admin)
    )
    assert minted.status_code == 201, minted.text
    allowed = await api_client.get(url, params={"token": minted.json()["token"]})
    assert allowed.status_code == 200

    other = await api_client.post(
        "/api/v1/auth/media-tokens", json={"item_id": other_item_id}, headers=_bearer(admin)
    )
    denied = await api_client.get(url, params={"token": other.json()["token"]})
    assert denied.status_code == 404


async def test_subtitles_enforce_library_access(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, _, file_id, _ = await _seed(
        db_settings, db_sessionmaker, sidecars={"The Matrix (1999).srt": SRT}
    )
    created = await api_client.post(
        "/api/v1/users",
        json={
            "username": "outsider",
            "password": "correct horse",
            "role": "user",
            "library_ids": [],
        },
        headers=_bearer(admin),
    )
    assert created.status_code == 201, created.text
    outsider = await api_client.post(
        "/api/v1/auth/login", json={"username": "outsider", "password": "correct horse"}
    )
    url = f"/api/v1/subtitles/{encode_subtitle_id(file_id, 'The Matrix (1999).srt')}.vtt"

    assert (await api_client.get(url, headers=_bearer(admin))).status_code == 200
    assert (await api_client.get(url, headers=_bearer(outsider.json()))).status_code == 404


async def test_forged_ids_and_stale_sidecars_return_404(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, _, file_id, video = await _seed(
        db_settings, db_sessionmaker, sidecars={"The Matrix (1999).srt": SRT}
    )
    (video.parent / "Unrelated.srt").write_text(SRT, encoding="utf-8")
    headers = _bearer(admin)

    assert (
        await api_client.get("/api/v1/subtitles/not-an-id.vtt", headers=headers)
    ).status_code == 404
    forged = f"/api/v1/subtitles/{encode_subtitle_id(file_id, 'Unrelated.srt')}.vtt"
    assert (await api_client.get(forged, headers=headers)).status_code == 404
    traversal = f"/api/v1/subtitles/{encode_subtitle_id(file_id, '../outside.srt')}.vtt"
    assert (await api_client.get(traversal, headers=headers)).status_code == 404

    url = f"/api/v1/subtitles/{encode_subtitle_id(file_id, 'The Matrix (1999).srt')}.vtt"
    assert (await api_client.get(url, headers=headers)).status_code == 200
    (video.parent / "The Matrix (1999).srt").unlink()
    assert (await api_client.get(url, headers=headers)).status_code == 404


async def test_tampered_file_paths_are_not_served(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    admin = await _setup(api_client)
    _, _, file_id, video = await _seed(
        db_settings, db_sessionmaker, sidecars={"The Matrix (1999).srt": SRT}
    )
    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"video")
    (tmp_path / "outside.srt").write_text(SRT, encoding="utf-8")

    async with db_sessionmaker() as session:
        row = await session.get(MediaFile, file_id)
        assert row is not None
        row.path = str(outside)
        await session.commit()

    url = f"/api/v1/subtitles/{encode_subtitle_id(file_id, 'outside.srt')}.vtt"
    assert (await api_client.get(url, headers=_bearer(admin))).status_code == 404
    assert video.exists()


async def test_oversized_subtitles_are_rejected(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, _, file_id, _ = await _seed(
        db_settings,
        db_sessionmaker,
        sidecars={"The Matrix (1999).srt": SRT + ("x" * 512)},
    )
    db_settings.subtitle_max_bytes = 64
    url = f"/api/v1/subtitles/{encode_subtitle_id(file_id, 'The Matrix (1999).srt')}.vtt"
    response = await api_client.get(url, headers=_bearer(admin))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "subtitle_too_large"
