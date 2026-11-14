"""Integration tests for container font attachment extraction and caching."""

import subprocess
import uuid
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Library, LibraryPath, MediaFile, MediaItem, Movie
from app.services.media.probe import parse_probe_payload
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _create_mkv_with_font(video_path: Path, font_bytes: bytes, font_name: str) -> None:
    font_path = video_path.parent / font_name
    font_path.write_bytes(font_bytes)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "nullsrc=s=64x64:d=1",
            "-attach",
            str(font_path),
            "-metadata:s:t",
            "mimetype=font/ttf",
            "-c:v",
            "libx264",
            str(video_path),
        ],
        check=True,
        capture_output=True,
    )
    font_path.unlink()


async def _seed(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    font_bytes: bytes = b"MOCK_OPENTYPE_FONT_DATA",
    font_name: str = "TestFont.ttf",
) -> tuple[str, str, str, Path]:
    root = settings.media_dir / f"Movies {uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    video = root / "Movie.With.Font.1999.mkv"
    _create_mkv_with_font(video, font_bytes, font_name)
    stat = video.stat()

    # Pre-populate streams_json to reflect ffprobe structure
    probe_dict = {
        "format": {"format_name": "matroska", "duration": "1.0"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            {
                "index": 1,
                "codec_type": "attachment",
                "tags": {"filename": font_name, "mimetype": "font/ttf"},
            },
        ],
        "chapters": [],
    }
    probe_res = parse_probe_payload(probe_dict)

    async with sessionmaker() as session:
        library = Library(name=f"Movies {uuid.uuid4().hex[:8]}", type="movie")
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.flush()

        item = MediaItem(
            library_id=library.id,
            type="movie",
            title="Movie with Font",
            sort_title=sort_title("Movie with Font"),
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
            duration_seconds=1.0,
            streams_json=probe_res.model_dump(mode="json"),
        )
        session.add(media_file)
        await session.commit()
        return library.id, item.id, media_file.id, video


async def test_attachment_serving_and_disk_cache(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    tokens = await _setup(api_client)
    expected_bytes = b"MOCK_OPENTYPE_FONT_DATA_42"
    _, item_id, file_id, _ = await _seed(
        db_settings,
        db_sessionmaker,
        font_bytes=expected_bytes,
        font_name="CustomFont.ttf",
    )

    url = f"/api/v1/media/{file_id}/attachments/1"
    res = await api_client.get(url, headers=_bearer(tokens))
    assert res.status_code == 200, res.text
    assert res.content == expected_bytes
    assert res.headers["content-type"].startswith("font/ttf")
    assert "max-age=86400" in res.headers["cache-control"]

    # Verify cache file exists
    cache_file = Path(db_settings.transcode_dir) / "fonts" / f"{file_id}_1.bin"
    assert cache_file.is_file()
    assert cache_file.read_bytes() == expected_bytes

    # Second request should serve from cache
    res2 = await api_client.get(url, headers=_bearer(tokens))
    assert res2.status_code == 200
    assert res2.content == expected_bytes


async def test_attachment_serving_with_media_token(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    tokens = await _setup(api_client)
    expected_bytes = b"MEDIA_TOKEN_FONT_BYTES"
    _, item_id, file_id, _ = await _seed(
        db_settings,
        db_sessionmaker,
        font_bytes=expected_bytes,
        font_name="MediaToken.otf",
    )

    minted = await api_client.post(
        "/api/v1/auth/media-tokens", json={"item_id": item_id}, headers=_bearer(tokens)
    )
    assert minted.status_code == 201
    media_token = minted.json()["token"]

    res = await api_client.get(f"/api/v1/media/{file_id}/attachments/1?token={media_token}")
    assert res.status_code == 200, res.text
    assert res.content == expected_bytes


async def test_attachment_not_found_cases(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    tokens = await _setup(api_client)
    _, _, file_id, _ = await _seed(db_settings, db_sessionmaker)

    # Missing attachment index
    res = await api_client.get(f"/api/v1/media/{file_id}/attachments/99", headers=_bearer(tokens))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "attachment_not_found"

    # Missing file id
    res_fake = await api_client.get(
        "/api/v1/media/018f0000-0000-7000-8000-000000000000/attachments/1",
        headers=_bearer(tokens),
    )
    assert res_fake.status_code == 404
    assert res_fake.json()["error"]["code"] == "file_not_found"
