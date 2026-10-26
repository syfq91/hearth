"""Phase 5 acceptance tests verifying all three acceptance criteria.

Criteria:
1. Native music API endpoints (browsing, search, playlist management, audio streaming,
   play tracking) pass automated tests against a fixture library.
2. Audio streaming supports range requests and returns accurate durations and content lengths.
3. A 5,000-track generated library scans in under 2 minutes and album list queries
   return in under 200 ms.
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from mutagen.id3 import ID3, TALB, TCON, TDRC, TIT2, TPE1, TPOS, TRCK
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.secrets import load_keys
from app.core.tokens import create_media_token
from app.db.models import Album, Artist, Library, LibraryPath, MediaFile, MediaItem, Track
from app.services.scanner.scanner import LibraryScanner

ADMIN = {"username": "admin", "password": "correct horse"}
NOW = datetime.now(UTC)


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    return response.json()


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _create_minimal_mp3(path: Path) -> None:
    frame = b"\xff\xfb\x90\x00" + (b"\x00" * 413)
    path.write_bytes(frame * 5)


def _create_tagged_mp3(
    path: Path,
    title: str,
    artist: str,
    album: str,
    track_num: int,
    disc_num: int = 1,
    year: int = 2021,
    genre: str = "Electronic",
) -> None:
    _create_minimal_mp3(path)
    tags = ID3()
    tags.add(TIT2(encoding=3, text=title))
    tags.add(TPE1(encoding=3, text=artist))
    tags.add(TALB(encoding=3, text=album))
    tags.add(TRCK(encoding=3, text=str(track_num)))
    tags.add(TPOS(encoding=3, text=str(disc_num)))
    tags.add(TDRC(encoding=3, text=str(year)))
    tags.add(TCON(encoding=3, text=genre))
    tags.save(str(path))


# ==============================================================================
# Criterion 1: Native Music API against a Fixture Library
# ==============================================================================


@pytest.mark.asyncio
async def test_criterion1_native_music_api_against_fixture_library(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Criterion 1: Endpoints against scanned fixture library."""
    admin = await _setup(api_client)

    # 1. Create fixture library tree on disk
    root = db_settings.media_dir / f"music_fixture_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)

    # Artist 1: Radiohead - OK Computer
    r_dir = root / "Radiohead" / "OK Computer"
    r_dir.mkdir(parents=True, exist_ok=True)
    _create_tagged_mp3(
        r_dir / "01 - Airbag.mp3",
        title="Airbag",
        artist="Radiohead",
        album="OK Computer",
        track_num=1,
        year=1997,
        genre="Alternative Rock",
    )
    _create_tagged_mp3(
        r_dir / "02 - Paranoid Android.mp3",
        title="Paranoid Android",
        artist="Radiohead",
        album="OK Computer",
        track_num=2,
        year=1997,
        genre="Alternative Rock",
    )

    # Artist 2: Daft Punk - Discovery
    d_dir = root / "Daft Punk" / "Discovery"
    d_dir.mkdir(parents=True, exist_ok=True)
    _create_tagged_mp3(
        d_dir / "01 - One More Time.mp3",
        title="One More Time",
        artist="Daft Punk",
        album="Discovery",
        track_num=1,
        year=2001,
        genre="House",
    )

    # 2. Add library and scan via LibraryScanner
    lib_id = f"lib_{uuid.uuid4().hex[:8]}"
    async with db_sessionmaker() as session:
        session.add(Library(id=lib_id, name="Fixture Music", type="music"))
        session.add(LibraryPath(id=f"lp_{uuid.uuid4().hex[:8]}", library_id=lib_id, path=str(root)))
        await session.commit()

    scanner = LibraryScanner(db_settings, db_sessionmaker, matcher=None)
    stats = await scanner.scan_library(lib_id)
    assert stats.added == 3
    assert stats.added_items >= 5  # 2 artists + 2 albums + 3 tracks

    # 3. Browsing: Artists
    res = await api_client.get("/api/v1/artists", headers=_bearer(admin))
    assert res.status_code == 200
    artists_data = res.json()
    assert artists_data["total"] == 2
    artist_names = [a["name"] for a in artists_data["items"]]
    assert "Radiohead" in artist_names
    assert "Daft Punk" in artist_names
    radiohead_id = next(a["id"] for a in artists_data["items"] if a["name"] == "Radiohead")

    # Artist detail
    res = await api_client.get(f"/api/v1/artists/{radiohead_id}", headers=_bearer(admin))
    assert res.status_code == 200
    artist_detail = res.json()
    assert artist_detail["name"] == "Radiohead"
    assert len(artist_detail["albums"]) == 1
    assert artist_detail["albums"][0]["title"] == "OK Computer"
    assert len(artist_detail["top_tracks"]) == 2

    # 4. Browsing: Albums
    res = await api_client.get("/api/v1/albums", headers=_bearer(admin))
    assert res.status_code == 200
    albums_data = res.json()
    assert albums_data["total"] == 2
    ok_comp = next(a for a in albums_data["items"] if a["title"] == "OK Computer")
    assert ok_comp["artist_name"] == "Radiohead"
    assert ok_comp["track_count"] == 2
    ok_comp_id = ok_comp["id"]

    # Album detail
    res = await api_client.get(f"/api/v1/albums/{ok_comp_id}", headers=_bearer(admin))
    assert res.status_code == 200
    alb_detail = res.json()
    assert alb_detail["title"] == "OK Computer"
    assert len(alb_detail["discs"]) == 1
    assert len(alb_detail["discs"][0]["tracks"]) == 2
    tracks = alb_detail["discs"][0]["tracks"]
    track1_id = tracks[0]["id"]
    track2_id = tracks[1]["id"]
    assert tracks[0]["title"] == "Airbag"
    assert tracks[1]["title"] == "Paranoid Android"

    # 5. Browsing: Tracks
    res = await api_client.get(f"/api/v1/tracks?album_id={ok_comp_id}", headers=_bearer(admin))
    assert res.status_code == 200
    assert res.json()["total"] == 2

    # Track detail
    res = await api_client.get(f"/api/v1/tracks/{track1_id}", headers=_bearer(admin))
    assert res.status_code == 200
    track_detail = res.json()
    assert track_detail["title"] == "Airbag"
    assert track_detail["stream_url"] == f"/api/v1/stream/audio/{track1_id}"

    # 6. Search
    res = await api_client.get("/api/v1/music/search?q=paranoid", headers=_bearer(admin))
    assert res.status_code == 200
    search_data = res.json()
    assert len(search_data["tracks"]) == 1
    assert search_data["tracks"][0]["title"] == "Paranoid Android"

    # 7. Shelves: Random & Recently Added
    res = await api_client.get("/api/v1/music/random", headers=_bearer(admin))
    assert res.status_code == 200
    assert len(res.json()["albums"]) >= 1

    res = await api_client.get("/api/v1/music/recently-added", headers=_bearer(admin))
    assert res.status_code == 200
    assert len(res.json()) >= 1

    # 8. Playlist Management
    res = await api_client.post(
        "/api/v1/playlists",
        json={"name": "Roadtrip Mix", "description": "Great songs"},
        headers=_bearer(admin),
    )
    assert res.status_code == 201
    p_id = res.json()["id"]

    res = await api_client.post(
        f"/api/v1/playlists/{p_id}/items",
        json={"item_ids": [track1_id, track2_id]},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    p_detail = res.json()
    assert len(p_detail["items"]) == 2
    item1_id = p_detail["items"][0]["id"]
    item2_id = p_detail["items"][1]["id"]

    # Reorder
    res = await api_client.put(
        f"/api/v1/playlists/{p_id}/items/reorder",
        json={"item_ids": [item2_id, item1_id]},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    assert res.json()["items"][0]["id"] == item2_id

    # 9. Audio Streaming
    stream_res = await api_client.get(
        f"/api/v1/stream/audio/{track1_id}",
        headers={**_bearer(admin), "Range": "bytes=0-199"},
    )
    assert stream_res.status_code == 206
    assert stream_res.headers["Content-Length"] == "200"

    # 10. Play Tracking & Favorites
    res = await api_client.post(
        f"/api/v1/items/{track1_id}/favorite",
        json={"favorite": True},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    assert res.json()["favorite"] is True

    # Play progress report
    res = await api_client.post(
        f"/api/v1/items/{track1_id}/progress",
        json={"position_ms": 1000, "state": "playing"},
        headers=_bearer(admin),
    )
    assert res.status_code == 200
    assert res.json()["play_count"] == 1


# ==============================================================================
# Criterion 2: Range Requests, Accurate Durations, and Content Lengths
# ==============================================================================


@pytest.mark.asyncio
async def test_criterion2_audio_streaming_range_requests_durations_content_length(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Criterion 2: Range requests, accurate durations, and content lengths."""
    admin = await _setup(api_client)

    root = db_settings.media_dir / f"stream_test_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)
    audio_file = root / "stream.flac"
    content = b"".join(bytes([i % 256]) for i in range(4096))  # Exactly 4096 bytes
    audio_file.write_bytes(content)

    lib_id = f"lib_{uuid.uuid4().hex[:8]}"
    art_id = f"art_{uuid.uuid4().hex[:8]}"
    alb_id = f"alb_{uuid.uuid4().hex[:8]}"
    tr_id = f"tr_{uuid.uuid4().hex[:8]}"
    f_id = f"f_{uuid.uuid4().hex[:8]}"
    expected_duration = 142.75

    async with db_sessionmaker() as session:
        session.add(Library(id=lib_id, name="Audio Lib", type="music"))
        session.add(LibraryPath(id=f"lp_{uuid.uuid4().hex[:8]}", library_id=lib_id, path=str(root)))

        art_item = MediaItem(
            id=art_id, library_id=lib_id, type="artist", title="Artist", sort_title="Artist"
        )
        art = Artist(item_id=art_id)
        alb_item = MediaItem(
            id=alb_id, library_id=lib_id, type="album", title="Album", sort_title="Album"
        )
        alb = Album(item_id=alb_id, artist_id=art_id)
        tr_item = MediaItem(
            id=tr_id,
            library_id=lib_id,
            type="track",
            title="Stream Track",
            sort_title="Stream Track",
            runtime_seconds=int(expected_duration),
        )
        session.add_all([art_item, art, alb_item, alb, tr_item])
        await session.flush()

        media_file = MediaFile(
            id=f_id,
            item_id=tr_id,
            path=str(audio_file),
            size_bytes=len(content),
            mtime=1.0,
            partial_hash=uuid.uuid4().hex * 2,
            container="flac",
            duration_seconds=expected_duration,
        )
        track = Track(
            item_id=tr_id,
            album_id=alb_id,
            artist_id=art_id,
            file_id=f_id,
            disc_number=1,
            track_number=1,
            duration_seconds=expected_duration,
            bitrate=960000,
            sample_rate=44100,
            channels=2,
        )
        session.add_all([media_file, track])
        await session.commit()

    # 1. Verify accurate duration returned from track detail
    res = await api_client.get(f"/api/v1/tracks/{tr_id}", headers=_bearer(admin))
    assert res.status_code == 200
    assert res.json()["duration_seconds"] == expected_duration

    # 2. HEAD request returns accurate Content-Length without body
    head_res = await api_client.head(f"/api/v1/stream/audio/{tr_id}", headers=_bearer(admin))
    assert head_res.status_code == 200
    assert head_res.headers["Content-Length"] == "4096"
    assert head_res.headers["Content-Type"] == "audio/flac"
    assert head_res.headers["Accept-Ranges"] == "bytes"
    assert len(head_res.content) == 0

    # 3. Full GET request returns entire length and accurate content
    full_res = await api_client.get(f"/api/v1/stream/audio/{tr_id}", headers=_bearer(admin))
    assert full_res.status_code == 200
    assert full_res.headers["Content-Length"] == "4096"
    assert full_res.content == content

    # 4. Standard Byte Range: bytes=200-799 (length 600)
    range_res = await api_client.get(
        f"/api/v1/stream/audio/{tr_id}",
        headers={**_bearer(admin), "Range": "bytes=200-799"},
    )
    assert range_res.status_code == 206
    assert range_res.headers["Content-Range"] == "bytes 200-799/4096"
    assert range_res.headers["Content-Length"] == "600"
    assert range_res.content == content[200:800]

    # 5. Open-ended range: bytes=3000- (from byte 3000 to 4095)
    open_res = await api_client.get(
        f"/api/v1/stream/audio/{tr_id}",
        headers={**_bearer(admin), "Range": "bytes=3000-"},
    )
    assert open_res.status_code == 206
    assert open_res.headers["Content-Range"] == "bytes 3000-4095/4096"
    assert open_res.headers["Content-Length"] == "1096"
    assert open_res.content == content[3000:4096]

    # 6. Suffix range: bytes=-500 (last 500 bytes)
    suffix_res = await api_client.get(
        f"/api/v1/stream/audio/{tr_id}",
        headers={**_bearer(admin), "Range": "bytes=-500"},
    )
    assert suffix_res.status_code == 206
    assert suffix_res.headers["Content-Range"] == "bytes 3596-4095/4096"
    assert suffix_res.headers["Content-Length"] == "500"
    assert suffix_res.content == content[3596:4096]

    # 7. Unsatisfiable range: bytes=5000- returns 416
    bad_range = await api_client.get(
        f"/api/v1/stream/audio/{tr_id}",
        headers={**_bearer(admin), "Range": "bytes=5000-"},
    )
    assert bad_range.status_code == 416
    assert bad_range.headers["Content-Range"] == "bytes */4096"

    # 8. Media token authentication in query string
    user_res = await api_client.get("/api/v1/auth/me", headers=_bearer(admin))
    user_id = user_res.json()["id"]

    keys = load_keys(db_settings)
    token, _ = create_media_token(
        user_id=user_id,
        token_version=0,
        settings=db_settings,
        key=keys.media,
        item_id=tr_id,
    )
    token_res = await api_client.get(
        f"/api/v1/stream/audio/{tr_id}?token={quote(token, safe='')}",
        headers={"Range": "bytes=0-99"},
    )
    assert token_res.status_code == 206
    assert token_res.headers["Content-Length"] == "100"
    assert token_res.content == content[0:100]


# ==============================================================================
# Criterion 3: 5,000-Track Library Scan (< 2 min) and Album Query (< 200 ms)
# ==============================================================================


@pytest.mark.asyncio
async def test_criterion3_benchmark_5000_tracks_scan_under_2_minutes_and_album_query_under_200ms(
    api_client: httpx.AsyncClient,
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Criterion 3: 5,000-track library scan (< 2 min) and album query (< 200 ms)."""
    admin = await _setup(api_client)

    root = db_settings.media_dir / f"bench_5000_{uuid.uuid4().hex[:8]}"
    root.mkdir(parents=True, exist_ok=True)

    # Pre-generate sample MP3 audio bytes (in-memory)
    sample_mp3 = (b"\xff\xfb\x90\x00" + (b"\x00" * 413)) * 5

    # 50 artists, 10 albums per artist, 10 tracks per album = 5,000 tracks
    for a_idx in range(50):
        artist_dir = root / f"Artist_{a_idx:02d}"
        for alb_idx in range(10):
            album_dir = artist_dir / f"Album_{alb_idx:02d} (2020)"
            album_dir.mkdir(parents=True, exist_ok=True)
            for tr_idx in range(1, 11):
                track_path = album_dir / f"{tr_idx:02d} - Track {tr_idx:02d}.mp3"
                track_path.write_bytes(sample_mp3)

    lib_id = f"lib_bench_{uuid.uuid4().hex[:8]}"
    async with db_sessionmaker() as session:
        session.add(Library(id=lib_id, name="5000 Tracks Bench", type="music"))
        session.add(LibraryPath(id=f"lp_{uuid.uuid4().hex[:8]}", library_id=lib_id, path=str(root)))
        await session.commit()

    # Part A: Measure 5,000-track scan time
    scanner = LibraryScanner(db_settings, db_sessionmaker, matcher=None)
    scan_start = time.perf_counter()
    stats = await scanner.scan_library(lib_id)
    scan_elapsed = time.perf_counter() - scan_start

    assert stats.added == 5000
    assert stats.added_items == 5550  # 50 artists + 500 albums + 5,000 tracks
    # Assert scans in under 2 minutes (120 seconds)
    assert scan_elapsed < 120.0, f"5,000-track scan took {scan_elapsed:.2f}s, exceeding 120s budget"

    # Part B: Measure album list query time via API (< 200 ms)
    # Warmup
    warmup_res = await api_client.get("/api/v1/albums?limit=50", headers=_bearer(admin))
    assert warmup_res.status_code == 200
    assert warmup_res.json()["total"] == 500

    query_durations: list[float] = []
    for _ in range(10):
        q_start = time.perf_counter()
        res = await api_client.get("/api/v1/albums?limit=50", headers=_bearer(admin))
        q_elapsed = time.perf_counter() - q_start
        assert res.status_code == 200
        assert len(res.json()["items"]) == 50
        query_durations.append(q_elapsed)

    avg_query_ms = (sum(query_durations) / len(query_durations)) * 1000
    max_query_ms = max(query_durations) * 1000

    # Assert album list queries return in under 200 ms
    assert max_query_ms < 200.0, (
        f"Album list query took {max_query_ms:.2f}ms (avg {avg_query_ms:.2f}ms), "
        "exceeding 200ms budget"
    )
