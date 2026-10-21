"""Schema-level tests for the Phase 1 and Phase 2 models."""

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AuditLog,
    Genre,
    Image,
    ItemGenre,
    Job,
    Library,
    LibraryAccess,
    LibraryPath,
    MediaFile,
    MediaItem,
    Movie,
    ProviderCache,
    RefreshToken,
    Setting,
    User,
    UserItemState,
)


async def test_user_defaults_and_relationships(db_session: AsyncSession) -> None:
    user = User(username="alice", password_hash="hash")
    db_session.add(user)
    await db_session.flush()

    assert user.id
    assert user.role == "user"
    assert user.disabled is False
    assert user.allow_transcode is True
    assert user.token_version == 0
    assert user.created_at.tzinfo is not None


async def test_library_paths_and_access_cascade(db_session: AsyncSession) -> None:
    user = User(username="bob", password_hash="hash")
    library = Library(name="Movies", type="movie")
    library.paths.append(LibraryPath(path="/media/movies"))
    library.access.append(LibraryAccess(user=user))
    db_session.add(library)
    await db_session.commit()

    loaded = await db_session.scalar(select(Library).where(Library.name == "Movies"))
    assert loaded is not None
    assert [path.path for path in loaded.paths] == ["/media/movies"]
    assert loaded.access[0].user.username == "bob"

    await db_session.delete(loaded)
    await db_session.commit()
    assert await db_session.scalar(select(func.count()).select_from(LibraryPath)) == 0
    assert await db_session.scalar(select(func.count()).select_from(LibraryAccess)) == 0
    assert await db_session.scalar(select(func.count()).select_from(User)) == 1


async def test_refresh_token_and_utc_timestamps(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime, timedelta

    user = User(username="carol", password_hash="hash")
    db_session.add(user)
    await db_session.flush()
    token = RefreshToken(
        user_id=user.id,
        family_id="family",
        token_hash="a" * 64,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add(token)
    await db_session.commit()
    db_session.expunge_all()

    loaded = await db_session.scalar(select(RefreshToken))
    assert loaded is not None
    assert loaded.expires_at.tzinfo is not None


async def test_job_defaults(db_session: AsyncSession) -> None:
    job = Job(type="noop", payload={"value": 1})
    db_session.add(job)
    await db_session.flush()

    assert job.status == "pending"
    assert job.progress == 0.0
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.cancel_requested is False
    assert job.run_after.tzinfo is not None


async def test_setting_and_audit_round_trip(db_session: AsyncSession) -> None:
    db_session.add(Setting(key="scan.interval", value="6"))
    db_session.add(AuditLog(action="library.create", target_type="library", details={"name": "TV"}))
    await db_session.commit()

    setting = await db_session.get(Setting, "scan.interval")
    audit = await db_session.scalar(select(AuditLog))
    assert setting is not None and setting.value == "6"
    assert audit is not None and audit.details == {"name": "TV"}
    assert audit.created_at.tzinfo is not None


async def _movie_fixture(db_session: AsyncSession) -> tuple[MediaItem, User]:
    user = User(username="dana", password_hash="hash")
    library = Library(name="Movie library", type="movie")
    library.paths.append(LibraryPath(path="/media/movies"))
    db_session.add_all([user, library])
    await db_session.flush()
    item = MediaItem(
        library_id=library.id,
        type="movie",
        title="The Matrix",
        sort_title="matrix",
        year=1999,
        overview="A hacker learns the truth.",
        runtime_seconds=8160,
        provider_ids={"tmdb": "603"},
    )
    item.movie = Movie(edition="Director's Cut", release_date="1999-03-31")
    item.files.append(
        MediaFile(
            path="/media/movies/The Matrix (1999)/The Matrix.mkv",
            size_bytes=1234,
            mtime=100.0,
            partial_hash="a" * 64,
            container="matroska,webm",
            duration_seconds=8160.0,
            streams_json={"streams": []},
        )
    )
    item.images.append(
        Image(
            kind="poster",
            provider="tmdb",
            local_path="/config/cache/images/poster.webp",
            width=500,
            height=750,
            blurhash="LEHV6nWB2yk8pyo0adR*.7kCMdnj",
        )
    )
    db_session.add(item)
    await db_session.flush()
    genre = Genre(name="Science Fiction")
    db_session.add(genre)
    await db_session.flush()
    db_session.add(ItemGenre(item_id=item.id, genre_id=genre.id))
    db_session.add(UserItemState(user_id=user.id, item_id=item.id, played=True, position_ms=4200))
    await db_session.commit()
    return item, user


async def test_catalog_models_and_defaults(db_session: AsyncSession) -> None:
    item, user = await _movie_fixture(db_session)
    db_session.expunge_all()

    loaded = await db_session.scalar(select(MediaItem).where(MediaItem.id == item.id))
    assert loaded is not None
    assert loaded.match_state == "pending"
    assert loaded.match_attempts == 0
    assert loaded.added_at.tzinfo is not None
    library = await db_session.get(Library, loaded.library_id)
    assert library is not None
    assert library.last_scan_status is None
    assert library.last_scan_stats == {}
    assert loaded.movie is not None and loaded.movie.edition == "Director's Cut"
    assert len(loaded.files) == 1 and loaded.files[0].missing_since is None
    assert loaded.images[0].blurhash is not None
    assert [genre.name for genre in loaded.genres] == ["Science Fiction"]

    state = await db_session.get(UserItemState, (user.id, loaded.id))
    assert state is not None
    assert state.played is True
    assert state.position_ms == 4200
    assert state.updated_at.tzinfo is not None


async def test_catalog_cascades_from_item(db_session: AsyncSession) -> None:
    item, user = await _movie_fixture(db_session)

    await db_session.delete(item)
    await db_session.commit()

    for model in (Movie, MediaFile, Image, ItemGenre, UserItemState):
        assert await db_session.scalar(select(func.count()).select_from(model)) == 0
    assert await db_session.get(User, user.id) is not None
    assert await db_session.scalar(select(func.count()).select_from(Genre)) == 1


async def test_media_fts_stays_in_sync_and_ignores_diacritics(db_session: AsyncSession) -> None:
    library = Library(name="FTS library", type="movie")
    db_session.add(library)
    await db_session.flush()
    item = MediaItem(
        library_id=library.id,
        type="movie",
        title="Amélie",
        sort_title="amelie",
    )
    db_session.add(item)
    await db_session.commit()

    async def matches(term: str) -> int:
        return int(
            await db_session.scalar(
                text("SELECT count(*) FROM media_items_fts WHERE media_items_fts MATCH :term"),
                {"term": term},
            )
            or 0
        )

    assert await matches("amelie") == 1
    assert await matches("matrix") == 0

    item.title = "The Matrix"
    item.sort_title = "matrix"
    await db_session.commit()
    assert await matches("amelie") == 0
    assert await matches("matrix") == 1

    await db_session.delete(item)
    await db_session.commit()
    assert await matches("matrix") == 0


async def test_provider_cache_round_trip(db_session: AsyncSession) -> None:
    from datetime import UTC, datetime, timedelta

    cache = ProviderCache(
        provider="tmdb",
        key="movie:603",
        payload={"title": "The Matrix"},
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db_session.add(cache)
    await db_session.commit()

    loaded = await db_session.get(ProviderCache, ("tmdb", "movie:603"))
    assert loaded is not None
    assert loaded.payload["title"] == "The Matrix"
    assert loaded.fetched_at.tzinfo is not None
