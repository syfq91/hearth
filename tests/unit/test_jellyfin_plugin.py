"""Unit tests for Jellyfin plugin auth, token isolation, and DTO mapping."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.requests import Request

from app.core.config import Settings
from app.core.errors import UnauthorizedError
from app.core.tokens import create_access_token, decode_access_token
from app.db.models import Episode, MediaFile, MediaItem, Movie, Season, Series, UserItemState
from plugins.jellyfin.auth import (
    JellyfinAuthHeader,
    create_jellyfin_token,
    decode_jellyfin_token,
    parse_jellyfin_auth_header,
)
from plugins.jellyfin.mapper import (
    TICKS_PER_SECOND,
    format_jf_datetime,
    media_item_to_dto,
    ms_to_ticks,
    seconds_to_ticks,
    ticks_to_ms,
)


def _make_request(headers: dict[str, str], query_string: str = "") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/Items",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
        "query_string": query_string.encode("latin-1"),
    }
    return Request(scope)


def test_parse_jellyfin_auth_header_variants() -> None:
    req1 = _make_request(
        {
            "X-Emby-Authorization": (
                'MediaBrowser Client="Findroid", Device="Pixel", '
                'DeviceId="dev-1", Version="0.14.0", Token="tok-123"'
            )
        }
    )
    parsed1 = parse_jellyfin_auth_header(req1)
    assert parsed1.client == "Findroid"
    assert parsed1.device == "Pixel"
    assert parsed1.device_id == "dev-1"
    assert parsed1.version == "0.14.0"
    assert parsed1.token == "tok-123"

    req2 = _make_request({"X-Emby-Token": "tok-456"})
    assert parse_jellyfin_auth_header(req2).token == "tok-456"

    req3 = _make_request({}, query_string="api_key=tok-789")
    assert parse_jellyfin_auth_header(req3).token == "tok-789"


def test_jellyfin_token_audience_isolation(tmp_path: Path) -> None:
    settings = Settings(env="testing", data_dir=tmp_path, db_path=tmp_path / "h.db")
    key = b"0" * 32
    header = JellyfinAuthHeader(client="Swiftfin", device="AppleTV", device_id="atv-1")

    jf_token, _ = create_jellyfin_token(
        user_id="user-1",
        token_version=3,
        auth_header=header,
        settings=settings,
        key=key,
    )
    claims = decode_jellyfin_token(jf_token, settings=settings, key=key)
    assert claims.user_id == "user-1"
    assert claims.token_version == 3
    assert claims.client == "Swiftfin"

    # Jellyfin token must NOT be accepted as a Hearth API access token
    with pytest.raises(UnauthorizedError):
        decode_access_token(jf_token, settings=settings, key=key)

    # Hearth API access token must NOT be accepted as a Jellyfin token
    hearth_token, _ = create_access_token(
        user_id="user-1",
        role="user",
        token_version=3,
        settings=settings,
        key=key,
    )
    with pytest.raises(UnauthorizedError):
        decode_jellyfin_token(hearth_token, settings=settings, key=key)


def test_tick_conversions_and_datetime() -> None:
    assert seconds_to_ticks(12.5) == int(12.5 * TICKS_PER_SECOND)
    assert seconds_to_ticks(None) is None
    assert ms_to_ticks(1500) == 15_000_000
    assert ticks_to_ms(15_000_000) == 1500
    assert format_jf_datetime(datetime(2026, 5, 1, 12, 30, 0, tzinfo=UTC)) == (
        "2026-05-01T12:30:00.0000000Z"
    )


def test_media_item_to_dto_movie_and_episode() -> None:
    movie_file = MediaFile(
        id="file-1",
        item_id="movie-1",
        path="/media/movies/Inception (2010).mkv",
        size_bytes=1_000_000,
        mtime=1.0,
        partial_hash="abc",
        container="matroska",
        duration_seconds=8880.0,
        bit_rate=5_000_000,
        streams_json={
            "streams": [
                {
                    "index": 0,
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "is_default": True,
                },
                {
                    "index": 1,
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "language": "eng",
                    "channels": 2,
                    "is_default": True,
                },
            ]
        },
    )
    movie_item = MediaItem(
        id="movie-1",
        library_id="lib-1",
        type="movie",
        title="Inception",
        sort_title="inception",
        year=2010,
        overview="Dream heist.",
        rating=8.8,
        content_rating="PG-13",
        runtime_seconds=8880,
        added_at=datetime(2026, 1, 1, tzinfo=UTC),
        movie=Movie(item_id="movie-1", tagline="Your mind is the scene of the crime."),
        files=[movie_file],
        images=[],
        genres=[],
    )
    state = UserItemState(
        user_id="u-1",
        item_id="movie-1",
        played=False,
        position_ms=60_000,
        favorite=True,
        play_count=1,
    )
    dto = media_item_to_dto(
        movie_item,
        "srv-1",
        user_state=state,
        include_media_sources=True,
        token="jf-tok",
    )
    assert dto["Type"] == "Movie"
    assert dto["MediaType"] == "Video"
    assert dto["RunTimeTicks"] == 8880 * TICKS_PER_SECOND
    assert dto["UserData"]["PlaybackPositionTicks"] == 600_000_000
    assert dto["UserData"]["IsFavorite"] is True
    assert len(dto["MediaSources"]) == 1
    assert dto["MediaSources"][0]["DirectStreamUrl"].startswith("/Videos/movie-1/stream")

    series_item = MediaItem(
        id="ser-1",
        library_id="lib-2",
        type="series",
        title="Severance",
        sort_title="severance",
        added_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    series = Series(item_id="ser-1", item=series_item)
    season_item = MediaItem(
        id="sea-1",
        library_id="lib-2",
        type="season",
        title="Season 1",
        sort_title="season 1",
        added_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    season = Season(
        item_id="sea-1", series_id="ser-1", season_number=1, item=season_item, series=series
    )
    ep_item = MediaItem(
        id="ep-1",
        library_id="lib-2",
        type="episode",
        title="Good News About Hell",
        sort_title="good news about hell",
        added_at=datetime(2026, 1, 1, tzinfo=UTC),
        episode=Episode(
            item_id="ep-1",
            series_id="ser-1",
            season_id="sea-1",
            season_number=1,
            episode_number=1,
            series=series,
            season=season,
            file=None,
        ),
        files=[],
        images=[],
        genres=[],
    )
    ep_dto = media_item_to_dto(ep_item, "srv-1")
    assert ep_dto["Type"] == "Episode"
    assert ep_dto["SeriesName"] == "Severance"
    assert ep_dto["ParentIndexNumber"] == 1
    assert ep_dto["IndexNumber"] == 1
