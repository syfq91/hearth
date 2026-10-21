"""Unit tests for metadata scoring and the matcher state machine."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Genre,
    ItemGenre,
    ItemPerson,
    Library,
    MediaFile,
    MediaItem,
    Movie,
    Person,
    UserItemState,
)
from app.services.media.titles import sort_title
from app.services.metadata.base import (
    MovieMetadata,
    PersonCredit,
    ProviderImage,
    ProviderUnavailable,
    SearchResult,
)
from app.services.metadata.local import LocalProvider
from app.services.metadata.matching import MetadataMatcher, score_candidate
from tests.support import FakeArtwork, FakeProvider


async def _seed_item(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    title: str = "The Matrix",
    year: int | None = 1999,
    path: str | None = None,
    duration: float | None = 8160.0,
    state: str = "pending",
    next_match_at: datetime | None = None,
) -> tuple[str, str]:
    async with sessionmaker() as session:
        library = Library(name=f"Movies {uuid.uuid4().hex[:8]}", type="movie")
        session.add(library)
        await session.flush()
        item = MediaItem(
            library_id=library.id,
            type="movie",
            title=title,
            sort_title=sort_title(title),
            year=year,
            match_state=state,
            next_match_at=next_match_at or datetime.now(UTC),
        )
        item.movie = Movie()
        item.files.append(
            MediaFile(
                path=path or f"/media/{uuid.uuid4().hex}.mkv",
                size_bytes=1024,
                mtime=1.0,
                partial_hash="a" * 64,
                duration_seconds=duration,
            )
        )
        session.add(item)
        await session.commit()
        return item.id, library.id


def _details(**overrides: object) -> MovieMetadata:
    values: dict[str, object] = {
        "provider": "tmdb",
        "provider_id": "603",
        "title": "The Matrix",
        "year": 1999,
        "runtime_seconds": 8160,
        "release_date": "1999-03-31",
        "overview": "A hacker learns the truth.",
        "tagline": "Free your mind.",
        "rating": 8.2,
        "genres": ("Action", "Science Fiction"),
        "credits": (
            PersonCredit(name="Keanu Reeves", role="cast", character="Neo", provider_id="6384"),
            PersonCredit(name="Lana Wachowski", role="director", provider_id="9330"),
        ),
        "provider_ids": {"tmdb": "603", "imdb": "tt0133093"},
    }
    values.update(overrides)
    return MovieMetadata(**values)  # type: ignore[arg-type]


def test_score_candidate_rewards_exact_title_year_and_runtime() -> None:
    exact = score_candidate(
        "The Matrix", 1999, "The Matrix", 1999, runtime_seconds=8160, candidate_runtime_seconds=8160
    )
    same_title_wrong_year = score_candidate(
        "The Matrix", 1999, "The Matrix", 2019, runtime_seconds=8160, candidate_runtime_seconds=8160
    )
    near_year = score_candidate(
        "The Matrix", 1999, "The Matrix", 2000, runtime_seconds=8160, candidate_runtime_seconds=8160
    )
    other_movie = score_candidate(
        "The Matrix",
        1999,
        "Speed Racer",
        2008,
        runtime_seconds=8160,
        candidate_runtime_seconds=8100,
    )

    assert exact > 0.99
    assert same_title_wrong_year < 0.80
    assert near_year > 0.80
    assert other_movie < 0.80


def test_score_candidate_handles_missing_year_and_runtime() -> None:
    unknown = score_candidate("Arrival", None, "Arrival", None)
    assert 0.78 < unknown < 0.86


async def test_match_pending_applies_metadata_and_is_idempotent(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    item_id, _ = await _seed_item(db_sessionmaker)
    provider = FakeProvider(
        results=[SearchResult("tmdb", "603", "The Matrix", 1999)],
        details={"603": _details()},
        images=[ProviderImage(kind="poster", source_url="https://img/p.jpg", is_primary=True)],
    )
    artwork = FakeArtwork()
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[provider], artwork=artwork)

    stats = await matcher.match_pending()

    assert stats.attempted == 1
    assert stats.matched == 1
    assert stats.provider_calls == 3
    assert len(artwork.calls) == 1
    assert provider.search_calls == 1
    assert provider.detail_calls == 1
    assert provider.image_calls == 1

    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None
        assert item.match_state == "matched"
        assert item.provider_ids["tmdb"] == "603"
        assert item.provider_ids["imdb"] == "tt0133093"
        assert item.overview == "A hacker learns the truth."
        assert item.runtime_seconds == 8160
        assert item.matched_at is not None and item.next_match_at is None
        genres = list(await session.scalars(select(Genre).order_by(Genre.name)))
        assert [genre.name for genre in genres] == ["Action", "Science Fiction"]
        assert await session.scalar(select(func.count()).select_from(ItemGenre)) == 2
        people = list(await session.scalars(select(Person).order_by(Person.name)))
        assert {person.name for person in people} == {"Keanu Reeves", "Lana Wachowski"}
        assert await session.scalar(select(func.count()).select_from(ItemPerson)) == 2

    # A second pass must not call the provider for an already matched item.
    second = await matcher.match_pending()
    assert second.attempted == 0
    assert second.provider_calls == 0
    assert len(artwork.calls) == 1


async def test_provider_failure_leaves_item_pending_with_backoff(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    item_id, _ = await _seed_item(db_sessionmaker)
    provider = FakeProvider(error=ProviderUnavailable("TMDB API key is not configured"))
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[provider])

    stats = await matcher.match_pending()
    assert stats.pending == 1
    assert stats.provider_calls == 1

    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None
        assert item.match_state == "pending"
        assert item.match_attempts == 1
        assert item.match_error and "not configured" in item.match_error
        assert item.next_match_at is not None
        assert item.next_match_at > datetime.now(UTC)

    # The retry is far enough in the future that an immediate rescan is quiet.
    assert (await matcher.match_pending()).attempted == 0


async def test_no_candidates_is_unmatched(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    item_id, _ = await _seed_item(db_sessionmaker)
    provider = FakeProvider(results=[])
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[provider])

    stats = await matcher.match_pending()

    assert stats.unmatched == 1
    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None
        assert item.match_state == "unmatched"
        assert item.next_match_at is not None
        assert item.next_match_at > datetime.now(UTC) + timedelta(hours=1)


async def test_low_confidence_is_unmatched(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    item_id, _ = await _seed_item(db_sessionmaker)
    wrong = MovieMetadata(
        provider="tmdb",
        provider_id="1",
        title="Speed Racer",
        year=2008,
        runtime_seconds=8100,
    )
    provider = FakeProvider(
        results=[SearchResult("tmdb", "1", "Speed Racer", 2008)],
        details={"1": wrong},
    )
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[provider])

    stats = await matcher.match_pending()

    assert stats.unmatched == 1
    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None and item.match_state == "unmatched"


async def test_local_nfo_wins_without_remote_calls(
    db_settings: Settings,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    video = tmp_path / "The Matrix (1999).mkv"
    video.write_bytes(b"video")
    (tmp_path / "movie.nfo").write_text(
        "<movie><title>The Matrix</title><year>1999</year></movie>", encoding="utf-8"
    )
    item_id, _ = await _seed_item(db_sessionmaker, path=str(video))

    remote = FakeProvider(error=ProviderUnavailable("must not be called"))
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[LocalProvider(), remote])

    stats = await matcher.match_pending()

    assert stats.matched == 1
    assert remote.search_calls == 0
    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None
        assert item.match_state == "matched"
        assert item.provider_ids["nfo"].endswith("movie.nfo")


async def test_manual_match_bypasses_scoring_and_audits_state(
    db_settings: Settings, db_sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    item_id, _ = await _seed_item(db_sessionmaker)
    provider = FakeProvider(
        details={"603": _details()},
        images=[ProviderImage(kind="backdrop", source_url="https://img/b.jpg")],
    )
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[provider])

    result = await matcher.manual_match(item_id, "tmdb", "603")

    assert result.state == "matched"
    assert result.score is None
    async with db_sessionmaker() as session:
        item = await session.get(MediaItem, item_id)
        assert item is not None and item.match_state == "matched"


async def test_matched_item_survives_watch_state(
    db_sessionmaker: async_sessionmaker[AsyncSession], db_settings: Settings
) -> None:
    item_id, _ = await _seed_item(db_sessionmaker)
    async with db_sessionmaker() as session:
        from app.db.models import User

        user = User(username=f"u{uuid.uuid4().hex[:8]}", password_hash="hash")
        session.add(user)
        await session.flush()
        session.add(UserItemState(user_id=user.id, item_id=item_id, played=True, position_ms=42))
        await session.commit()

    provider = FakeProvider(
        results=[SearchResult("tmdb", "603", "The Matrix", 1999)],
        details={"603": _details()},
    )
    matcher = MetadataMatcher(db_settings, db_sessionmaker, providers=[provider])
    await matcher.match_pending()

    async with db_sessionmaker() as session:
        state = await session.scalar(select(UserItemState))
        assert state is not None and state.position_ms == 42
