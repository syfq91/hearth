"""Integration tests verifying direct play, streaming, and progress reporting for TV episodes."""

import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Episode,
    Library,
    LibraryPath,
    MediaFile,
    MediaItem,
    Season,
    Series,
)
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}
SRT_CONTENT = "1\n00:00:01,000 --> 00:00:04,000\nPreviously on Better Call Saul...\n"


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def test_tv_episode_playback_info_and_direct_streaming(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    tokens = await _setup(api_client)
    headers = _bearer(tokens)

    # 1. Create TV library on disk
    root = db_settings.media_dir / f"tv_{uuid.uuid4().hex[:8]}"
    show_dir = root / "Better Call Saul"
    show_dir.mkdir(parents=True, exist_ok=True)

    video_file = show_dir / "Better.Call.Saul.S01E01.mkv"
    video_content = b"VIDEO_EPISODE_CONTENT_DATA_12345" * 100
    video_file.write_bytes(video_content)

    sub_file = show_dir / "Better.Call.Saul.S01E01.en.srt"
    sub_file.write_text(SRT_CONTENT, encoding="utf-8")

    async with db_sessionmaker() as session:
        library = Library(name="TV Shows", type="tv")
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.flush()

        series_item = MediaItem(
            library_id=library.id,
            type="series",
            title="Better Call Saul",
            sort_title=sort_title("Better Call Saul"),
            year=2015,
            match_state="matched",
        )
        session.add(series_item)
        await session.flush()
        session.add(Series(item_id=series_item.id))

        season_item = MediaItem(
            library_id=library.id,
            type="season",
            title="Season 1",
            sort_title=sort_title("Season 1"),
            match_state="matched",
        )
        session.add(season_item)
        await session.flush()
        session.add(Season(item_id=season_item.id, series_id=series_item.id, season_number=1))

        ep_item = MediaItem(
            library_id=library.id,
            type="episode",
            title="Uno",
            sort_title=sort_title("Uno"),
            year=2015,
            runtime_seconds=3000,
            match_state="matched",
        )
        session.add(ep_item)
        await session.flush()

        media_file = MediaFile(
            item_id=ep_item.id,
            path=str(video_file),
            size_bytes=len(video_content),
            mtime=video_file.stat().st_mtime,
            partial_hash=uuid.uuid4().hex * 2,
            container="matroska",
            duration_seconds=3000.0,
        )
        session.add(media_file)
        await session.flush()

        session.add(
            Episode(
                item_id=ep_item.id,
                series_id=series_item.id,
                season_id=season_item.id,
                season_number=1,
                episode_number=1,
                file_id=media_file.id,
            )
        )
        await session.commit()
        ep_id = ep_item.id
        file_id = media_file.id

    # 2. Call playback-info for the episode
    pb_resp = await api_client.post(f"/api/v1/items/{ep_id}/playback-info", headers=headers)
    assert pb_resp.status_code == 200, pb_resp.text
    pb_data = pb_resp.json()
    assert pb_data["mode"] == "direct_play"
    assert pb_data["file"]["id"] == file_id
    assert pb_data["file"]["size_bytes"] == len(video_content)
    assert pb_data["url"].startswith(f"/api/v1/stream/{file_id}?token=")
    assert len(pb_data["subtitles"]) == 1
    sub_url = pb_data["subtitles"][0]["url"]

    # 3. Stream the file using the tokenized URL without auth headers
    stream_resp = await api_client.get(pb_data["url"])
    assert stream_resp.status_code == 200
    assert stream_resp.content == video_content

    # Range request
    range_resp = await api_client.get(pb_data["url"], headers={"Range": "bytes=0-99"})
    assert range_resp.status_code == 206
    assert range_resp.content == video_content[:100]

    # Subtitle request
    vtt_resp = await api_client.get(sub_url)
    assert vtt_resp.status_code == 200
    assert "WEBVTT" in vtt_resp.text
    assert "Previously on Better Call Saul" in vtt_resp.text


async def test_multi_episode_file_playback_and_progress_isolation(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    tokens = await _setup(api_client)
    headers = _bearer(tokens)

    root = db_settings.media_dir / f"tv_{uuid.uuid4().hex[:8]}"
    show_dir = root / "Friends"
    show_dir.mkdir(parents=True, exist_ok=True)

    # Multi-episode file representing S01E01E02
    multi_file = show_dir / "Friends.S01E01E02.mkv"
    multi_content = b"MULTI_EPISODE_FILE_PAYLOAD_CONTENT" * 50
    multi_file.write_bytes(multi_content)

    async with db_sessionmaker() as session:
        library = Library(name="TV Shows", type="tv")
        library.paths.append(LibraryPath(path=str(root)))
        session.add(library)
        await session.flush()

        series_item = MediaItem(
            library_id=library.id,
            type="series",
            title="Friends",
            sort_title=sort_title("Friends"),
            match_state="matched",
        )
        session.add(series_item)
        await session.flush()
        session.add(Series(item_id=series_item.id))

        season_item = MediaItem(
            library_id=library.id,
            type="season",
            title="Season 1",
            sort_title=sort_title("Season 1"),
            match_state="matched",
        )
        session.add(season_item)
        await session.flush()
        session.add(Season(item_id=season_item.id, series_id=series_item.id, season_number=1))

        # Episode 1 (Primary for MediaFile)
        ep1_item = MediaItem(
            library_id=library.id,
            type="episode",
            title="The One Where Monica Gets a Roommate",
            sort_title=sort_title("The One Where Monica Gets a Roommate"),
            runtime_seconds=2400,
            match_state="matched",
        )
        session.add(ep1_item)
        await session.flush()

        # Shared media file pointing to ep1_item.id
        shared_file = MediaFile(
            item_id=ep1_item.id,
            path=str(multi_file),
            size_bytes=len(multi_content),
            mtime=multi_file.stat().st_mtime,
            partial_hash=uuid.uuid4().hex * 2,
            container="matroska",
            duration_seconds=2400.0,
        )
        session.add(shared_file)
        await session.flush()

        session.add(
            Episode(
                item_id=ep1_item.id,
                series_id=series_item.id,
                season_id=season_item.id,
                season_number=1,
                episode_number=1,
                file_id=shared_file.id,
            )
        )

        # Episode 2 (Constituent referencing shared file_id)
        ep2_item = MediaItem(
            library_id=library.id,
            type="episode",
            title="The One with the Sonogram at the End",
            sort_title=sort_title("The One with the Sonogram at the End"),
            runtime_seconds=2400,
            match_state="matched",
        )
        session.add(ep2_item)
        await session.flush()

        session.add(
            Episode(
                item_id=ep2_item.id,
                series_id=series_item.id,
                season_id=season_item.id,
                season_number=1,
                episode_number=2,
                file_id=shared_file.id,
            )
        )
        await session.commit()
        ep1_id = ep1_item.id
        ep2_id = ep2_item.id
        file_id = shared_file.id

    # 1. Playback-info for Episode 2 (which does not own the MediaFile item_id directly)
    pb2_resp = await api_client.post(f"/api/v1/items/{ep2_id}/playback-info", headers=headers)
    assert pb2_resp.status_code == 200, pb2_resp.text
    pb2_data = pb2_resp.json()
    assert pb2_data["mode"] == "direct_play"
    assert pb2_data["file"]["id"] == file_id
    assert pb2_data["url"].startswith(f"/api/v1/stream/{file_id}?token=")

    # Stream using the token minted for Episode 2
    stream2_resp = await api_client.get(pb2_data["url"])
    assert stream2_resp.status_code == 200
    assert stream2_resp.content == multi_content

    # 2. Progress reporting on Episode 1 vs Episode 2
    # Watch Episode 1 to 50%
    prog1 = await api_client.post(
        f"/api/v1/items/{ep1_id}/progress",
        json={"position_ms": 1200000, "duration_ms": 2400000, "state": "paused"},
        headers=headers,
    )
    assert prog1.status_code == 200
    assert prog1.json()["watched"] is False
    assert prog1.json()["position_ms"] == 1200000

    # Watch Episode 2 past watched threshold (95%)
    prog2 = await api_client.post(
        f"/api/v1/items/{ep2_id}/progress",
        json={"position_ms": 2300000, "duration_ms": 2400000, "state": "paused"},
        headers=headers,
    )
    assert prog2.status_code == 200
    assert prog2.json()["watched"] is True
    assert prog2.json()["auto_watched"] is True

    # Check episode item endpoints
    ep1_detail = (await api_client.get(f"/api/v1/episodes/{ep1_id}", headers=headers)).json()
    assert ep1_detail["watch_state"]["watched"] is False
    assert ep1_detail["watch_state"]["position_ms"] == 1200000

    ep2_detail = (await api_client.get(f"/api/v1/episodes/{ep2_id}", headers=headers)).json()
    assert ep2_detail["watch_state"]["watched"] is True
