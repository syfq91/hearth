"""Integration tests for the playback-info contract."""

import uuid
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Library, LibraryPath, MediaFile, MediaItem, Movie
from app.services.media.titles import sort_title

ADMIN = {"username": "admin", "password": "correct horse"}

SRT = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"


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
    versions: int = 1,
    sidecar: bool = True,
) -> tuple[str, str, list[str], Path]:
    root = settings.media_dir / f"Movies {uuid.uuid4().hex[:8]}"
    root.mkdir()
    paths: list[Path] = []
    for index in range(versions):
        name = "The Matrix (1999).mp4" if index == 0 else f"The Matrix (1999) - v{index + 1}.mp4"
        path = root / name
        path.write_bytes(b"video" * 100)
        paths.append(path)
    if sidecar:
        (root / "The Matrix (1999).srt").write_text(SRT, encoding="utf-8")

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
            runtime_seconds=8160,
            match_state="matched",
        )
        item.movie = Movie()
        session.add(item)
        await session.flush()
        file_ids: list[str] = []
        for index, path in enumerate(paths):
            stat = path.stat()
            media_file = MediaFile(
                item_id=item.id,
                path=str(path),
                size_bytes=stat.st_size,
                mtime=stat.st_mtime,
                partial_hash=uuid.uuid4().hex * 2,
                container="mp4",
                duration_seconds=8160.0 - index,
            )
            session.add(media_file)
            await session.flush()
            file_ids.append(media_file.id)
        await session.commit()
        return library.id, item.id, file_ids, root


async def test_playback_info_returns_usable_direct_play_urls(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, file_ids, _ = await _seed(db_settings, db_sessionmaker)

    response = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info", headers=_bearer(admin)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "direct_play"
    assert body["file"] == {
        "id": file_ids[0],
        "container": "mp4",
        "duration_seconds": 8160.0,
        "size_bytes": 500,
    }
    assert body["url"].startswith(f"/api/v1/stream/{file_ids[0]}?token=")
    assert body["hls_url"] is None
    assert body["session_id"] is None
    assert body["expires_at"]
    assert body["reasons"] == []
    assert len(body["subtitles"]) == 1
    subtitle = body["subtitles"][0]
    assert subtitle["format"] == "srt"
    assert "?token=" in subtitle["url"]

    played = await api_client.get(body["url"])
    assert played.status_code == 200
    assert played.content == b"video" * 100

    captions = await api_client.get(subtitle["url"])
    assert captions.status_code == 200
    assert captions.text.startswith("WEBVTT")


async def test_playback_info_accepts_the_full_capability_profile(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, _, _ = await _seed(db_settings, db_sessionmaker, sidecar=False)
    capabilities = {
        "preset": "chrome",
        "containers": ["mp4", "webm"],
        "video_codecs": ["h264", "vp9", "av1"],
        "video_profiles": ["high", "main"],
        "audio_codecs": ["aac", "opus"],
        "max_width": 3840,
        "max_height": 2160,
        "max_bitrate": 40_000_000,
        "subtitle_formats": ["vtt"],
        "hdr": True,
        "hls_fmp4": True,
    }
    response = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        json={"capabilities": capabilities},
        headers=_bearer(admin),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "direct_play"
    assert body["subtitles"] == []

    invalid = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        json={"capabilities": {**capabilities, "max_width": 0}},
        headers=_bearer(admin),
    )
    assert invalid.status_code == 422


async def test_playback_info_selects_a_requested_version(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, file_ids, _ = await _seed(db_settings, db_sessionmaker, versions=2)
    headers = _bearer(admin)

    chosen = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        json={"file_id": file_ids[1]},
        headers=headers,
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["file"]["id"] == file_ids[1]
    assert f"/api/v1/stream/{file_ids[1]}" in chosen.json()["url"]

    unknown = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        json={"file_id": "missing"},
        headers=headers,
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "file_not_found"

    async with db_sessionmaker() as session:
        first = await session.get(MediaFile, file_ids[0])
        second = await session.get(MediaFile, file_ids[1])
        assert first is not None and second is not None
        first.missing_since = first.created_at
        second.missing_since = second.created_at
        await session.commit()

    gone = await api_client.post(f"/api/v1/items/{item_id}/playback-info", headers=headers)
    assert gone.status_code == 404


async def test_playback_info_enforces_access_and_path_containment(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    admin = await _setup(api_client)
    library_id, item_id, file_ids, _ = await _seed(db_settings, db_sessionmaker)
    assert library_id

    outsider = await api_client.post(
        "/api/v1/users",
        json={
            "username": "outsider",
            "password": "correct horse",
            "role": "user",
            "library_ids": [],
        },
        headers=_bearer(admin),
    )
    assert outsider.status_code == 201, outsider.text
    outsider_tokens = await api_client.post(
        "/api/v1/auth/login", json={"username": "outsider", "password": "correct horse"}
    )
    url = f"/api/v1/items/{item_id}/playback-info"

    assert (await api_client.post(url)).status_code == 401
    assert (await api_client.post(url, headers=_bearer(outsider_tokens.json()))).status_code == 404
    assert (
        await api_client.post("/api/v1/items/missing/playback-info", headers=_bearer(admin))
    ).status_code == 404

    outside = tmp_path / "outside.mkv"
    outside.write_bytes(b"secret")
    async with db_sessionmaker() as session:
        row = await session.get(MediaFile, file_ids[0])
        assert row is not None
        row.path = str(outside)
        await session.commit()

    tampered = await api_client.post(url, headers=_bearer(admin))
    assert tampered.status_code == 404
    assert tampered.json()["error"]["code"] == "file_not_found"


async def test_playback_info_transcoding_decision_and_track_selection(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, file_ids, root = await _seed(db_settings, db_sessionmaker)

    # Attach probe streams: 1 video (hevc), 2 audio (aac, dts), 1 subtitle (pgs)
    streams_json = {
        "container": "mp4",
        "duration_seconds": 120.0,
        "bit_rate": 8_000_000,
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "hevc",
                "profile": "Main",
                "width": 1920,
                "height": 1080,
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "is_default": True,
            },
            {
                "index": 2,
                "codec_type": "audio",
                "codec_name": "dts",
                "channels": 6,
            },
            {
                "index": 3,
                "codec_type": "subtitle",
                "codec_name": "hdmv_pgs_subtitle",
            },
        ],
    }
    async with db_sessionmaker() as session:
        f = await session.get(MediaFile, file_ids[0])
        assert f is not None
        f.streams_json = streams_json
        await session.commit()

    # 1. Chrome client cannot play HEVC natively -> transcode_hls
    res = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        json={"capabilities": {"preset": "chrome"}},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "transcode_hls"
    assert data["session_id"] is not None
    assert "/api/v1/hls/" in data["hls_url"]
    assert "VideoCodecNotSupported" in data["reasons"]

    # Stop the previous session so concurrency slots are not exhausted
    await api_client.post(f"/api/v1/hls/{data['session_id']}/stop", headers=_bearer(admin))

    # 2. Track selection: selecting DTS audio (index 2) and PGS subtitle (index 3)
    track_res = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        json={
            "capabilities": {"preset": "chrome"},
            "audio_track_index": 2,
            "subtitle_track_index": 3,
        },
        headers=_bearer(admin),
    )
    assert track_res.status_code == 200
    t_data = track_res.json()
    assert t_data["mode"] == "transcode_hls"
    assert "SubtitleBurnInRequired" in t_data["reasons"]


async def test_playback_info_user_agent_fallback_and_precedence(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, file_ids, _ = await _seed(db_settings, db_sessionmaker, sidecar=False)

    # Set media file to HEVC video + AAC audio
    streams_json = {
        "container": "mp4",
        "duration_seconds": 120.0,
        "size_bytes": 10_000_000,
        "streams": [
            {
                "index": 0,
                "codec_name": "hevc",
                "codec_type": "video",
                "profile": "Main",
                "width": 1920,
                "height": 1080,
                "bitrate": 4_000_000,
            },
            {
                "index": 1,
                "codec_name": "aac",
                "codec_type": "audio",
                "channels": 2,
                "sample_rate": 48000,
            },
        ],
    }
    async with db_sessionmaker() as session:
        f = await session.get(MediaFile, file_ids[0])
        assert f is not None
        f.streams_json = streams_json
        await session.commit()

    # 1. No capabilities provided, Safari UA -> Safari supports HEVC natively
    safari_ua = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15"
    )
    safari_headers = {**_bearer(admin), "User-Agent": safari_ua}
    res_safari = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        headers=safari_headers,
    )
    assert res_safari.status_code == 200
    assert res_safari.json()["mode"] == "direct_play"

    # 2. No capabilities provided, Chrome UA -> Chrome cannot play HEVC mp4 natively
    chrome_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    chrome_headers = {**_bearer(admin), "User-Agent": chrome_ua}
    res_chrome = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        headers=chrome_headers,
    )
    assert res_chrome.status_code == 200
    data_chrome = res_chrome.json()
    assert data_chrome["mode"] == "transcode_hls"
    assert "VideoCodecNotSupported" in data_chrome["reasons"]
    await api_client.post(f"/api/v1/hls/{data_chrome['session_id']}/stop", headers=_bearer(admin))

    # 3. Explicit capabilities WIN over UA preset:
    # Caller sends Chrome UA, but sends explicit capabilities supporting HEVC
    res_explicit = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        json={
            "capabilities": {
                "containers": ["mp4"],
                "video_codecs": ["hevc"],
                "audio_codecs": ["aac"],
            }
        },
        headers=chrome_headers,
    )
    assert res_explicit.status_code == 200
    assert res_explicit.json()["mode"] == "direct_play"

    # 4. Unrecognized UA falls back to unknown (unknown does not support HEVC)
    res_unknown = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        headers={**_bearer(admin), "User-Agent": "curl/7.88.1"},
    )
    assert res_unknown.status_code == 200
    data_unknown = res_unknown.json()
    assert data_unknown["mode"] == "transcode_hls"
    assert "VideoCodecNotSupported" in data_unknown["reasons"]
    await api_client.post(f"/api/v1/hls/{data_unknown['session_id']}/stop", headers=_bearer(admin))


async def test_playback_info_malformed_capabilities_returns_422(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, _, _ = await _seed(db_settings, db_sessionmaker, sidecar=False)

    # Bad type for max_width -> 422 with field path
    bad_res = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        json={"capabilities": {"max_width": "not-an-integer"}},
        headers=_bearer(admin),
    )
    assert bad_res.status_code == 422
    err_body = bad_res.json()
    errors = err_body["error"]["details"]["errors"]
    locs = [e["loc"] for e in errors]
    assert any("max_width" in loc for loc in locs)

    # Unknown fields are ignored
    unknown_field_res = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        json={"capabilities": {"random_field_xyz": 12345}},
        headers=_bearer(admin),
    )
    assert unknown_field_res.status_code == 200


async def test_playback_info_case_insensitive_query_params(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, file_ids, _ = await _seed(db_settings, db_sessionmaker, sidecar=False)

    # Media file is 4 Mbps H.264
    streams_json = {
        "container": "mp4",
        "duration_seconds": 120.0,
        "size_bytes": 60_000_000,
        "bit_rate": 4_000_000,
        "streams": [
            {
                "index": 0,
                "codec_name": "h264",
                "codec_type": "video",
                "profile": "High",
                "width": 1920,
                "height": 1080,
                "bitrate": 4_000_000,
            },
            {
                "index": 1,
                "codec_name": "aac",
                "codec_type": "audio",
                "channels": 2,
                "sample_rate": 48000,
            },
        ],
    }
    async with db_sessionmaker() as session:
        f = await session.get(MediaFile, file_ids[0])
        assert f is not None
        f.streams_json = streams_json
        await session.commit()

    # Client queries with VideoBitrate=1000000 (1 Mbps cap, lower than 4 Mbps media)
    # This should trigger transcode_hls with ContainerBitrateExceedsLimit
    res1 = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info?VideoBitrate=1000000",
        json={"capabilities": {"preset": "chrome"}},
        headers=_bearer(admin),
    )
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["mode"] == "transcode_hls"
    assert "ContainerBitrateExceedsLimit" in data1["reasons"]
    await api_client.post(f"/api/v1/hls/{data1['session_id']}/stop", headers=_bearer(admin))

    # Client queries with videoBitRate=1000000 (camelCase)
    res2 = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info?videoBitRate=1000000",
        json={"capabilities": {"preset": "chrome"}},
        headers=_bearer(admin),
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["mode"] == "transcode_hls"
    assert "ContainerBitrateExceedsLimit" in data2["reasons"]
    await api_client.post(f"/api/v1/hls/{data2['session_id']}/stop", headers=_bearer(admin))


async def test_playback_info_includes_font_attachments(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, file_ids, _ = await _seed(db_settings, db_sessionmaker, sidecar=False)

    streams_json = {
        "container": "matroska",
        "duration_seconds": 100.0,
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
            {
                "index": 2,
                "codec_type": "attachment",
                "tags": {"filename": "anime.ttf", "mimetype": "font/ttf"},
            },
        ],
        "chapters": [],
    }
    async with db_sessionmaker() as session:
        f = await session.get(MediaFile, file_ids[0])
        assert f is not None
        f.streams_json = streams_json
        await session.commit()

    res = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["attachments"]) == 1
    att = data["attachments"][0]
    assert att["index"] == 2
    assert att["filename"] == "anime.ttf"
    assert att["mime_type"] == "font/ttf"
    assert f"/api/v1/media/{file_ids[0]}/attachments/2?token=" in att["url"]


async def test_playback_info_force_burn_in_subtitles(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    admin = await _setup(api_client)
    _, item_id, file_ids, _ = await _seed(db_settings, db_sessionmaker, sidecar=False)

    streams_json = {
        "container": "mp4",
        "duration_seconds": 100.0,
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "pix_fmt": "yuv420p",
            },
            {
                "index": 1,
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "sample_rate": 48000,
            },
            {
                "index": 2,
                "codec_type": "subtitle",
                "codec_name": "ass",
                "tags": {"language": "eng"},
            },
        ],
        "chapters": [],
    }
    async with db_sessionmaker() as session:
        f = await session.get(MediaFile, file_ids[0])
        assert f is not None
        f.streams_json = streams_json
        await session.commit()

    # Query with force_burn_in_subtitles=True and subtitle_track_index=2
    res = await api_client.post(
        f"/api/v1/items/{item_id}/playback-info",
        json={
            "capabilities": {
                "preset": "chrome",
                "subtitle_profiles": [{"format": "ass", "method": "external"}],
            },
            "subtitle_track_index": 2,
            "force_burn_in_subtitles": True,
        },
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    data = res.json()
    assert data["mode"] == "transcode_hls"
    assert "SubtitleBurnInRequired" in data["reasons"]
    assert data["plan"]["subtitle_action"] == "burn_in"
    if data.get("session_id"):
        await api_client.post(f"/api/v1/hls/{data['session_id']}/stop", headers=_bearer(admin))
