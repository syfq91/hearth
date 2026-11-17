"""Unit tests for the scanner's parse phase and cancellation cache."""

from pathlib import Path
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.scanner.scanner as scanner_module
from app.core.config import Settings
from app.services.scanner.scanner import LibraryScanner, _cache_cancellation
from app.services.scanner.walker import ScannedFile


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        env="testing",
        log_level="WARNING",
        data_dir=tmp_path,
        db_path=tmp_path / "hearth.db",
        transcode_dir=tmp_path / "transcode",
        media_dir=tmp_path / "media",
        render_device=tmp_path / "renderD128",
        secret_key="test-secret-key",
    )


class _FakeTime:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def monotonic(self) -> float:
        return next(self._values)


async def test_cancellation_is_polled_at_most_once_per_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scanner_module, "time", _FakeTime(10.0, 10.2, 10.4, 10.6))
    calls = 0

    async def probe() -> bool:
        nonlocal calls
        calls += 1
        return False

    cached = _cache_cancellation(probe, interval=1.0)
    assert cached is not None

    for _ in range(4):
        assert await cached() is False
    assert calls == 1


async def test_cancellation_stays_true_once_observed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(scanner_module, "time", _FakeTime(10.0, 11.5))
    calls = 0

    async def probe() -> bool:
        nonlocal calls
        calls += 1
        return True

    cached = _cache_cancellation(probe, interval=1.0)
    assert cached is not None

    assert await cached() is True
    assert await cached() is True
    assert calls == 1


async def test_parse_files_precomputes_movie_and_episode_identities(
    settings: Settings, tmp_path: Path
) -> None:
    scanner = LibraryScanner(settings, cast(async_sessionmaker[AsyncSession], None))

    movie = tmp_path / "Movies" / "Test Movie (2020).mkv"
    movie.parent.mkdir(parents=True)
    movie.touch()
    episode = tmp_path / "TV" / "Test Show" / "Season 01" / "Test Show S01E02.mkv"
    episode.parent.mkdir(parents=True)
    episode.touch()

    parsed = await scanner._parse_files(
        "movie", [ScannedFile(path=str(movie), size_bytes=1, mtime=1.0)], None, None
    )
    assert parsed.movies[str(movie)].title == "Test Movie"
    assert parsed.movies[str(movie)].year == 2020

    parsed_tv = await scanner._parse_files(
        "tv", [ScannedFile(path=str(episode), size_bytes=1, mtime=1.0)], None, None
    )
    identity = parsed_tv.episodes[str(episode)]
    assert identity.series_title == "Test Show"
    assert identity.season_number == 1
    assert identity.primary_episode_number == 2
