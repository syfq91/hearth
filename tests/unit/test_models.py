"""Schema-level tests for the Phase 1 and Phase 2 models."""

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Album,
    Artist,
    AuditLog,
    Episode,
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
    Playlist,
    PlaylistItem,
    ProviderCache,
    RefreshToken,
    Season,
    Series,
    Setting,
    Track,
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


async def test_tv_models_relationships_and_cascades(db_session: AsyncSession) -> None:
    library = Library(name="TV Shows", type="tv")
    library.paths.append(LibraryPath(path="/media/tv"))
    db_session.add(library)
    await db_session.flush()

    # 1. Series
    series_item = MediaItem(
        library_id=library.id,
        type="series",
        title="Breaking Bad",
        sort_title="breaking bad",
        year=2008,
        overview="A high school chemistry teacher diagnosed with cancer turns to meth.",
        provider_ids={"tmdb": "1396"},
    )
    series_item.series = Series(
        original_title="Breaking Bad",
        status="Ended",
        first_air_date="2008-01-20",
        network="AMC",
    )
    db_session.add(series_item)
    await db_session.flush()

    # 2. Season 1
    season_item = MediaItem(
        library_id=library.id,
        type="season",
        title="Season 1",
        sort_title="season 01",
        year=2008,
        overview="Season 1 of Breaking Bad.",
    )
    season_item.season = Season(
        series_id=series_item.id,
        season_number=1,
        air_date="2008-01-20",
    )
    db_session.add(season_item)
    await db_session.flush()

    # 3. MediaFile shared by two episodes (multi-episode file)
    media_file = MediaFile(
        item_id=series_item.id,
        path="/media/tv/Breaking Bad/Season 1/Breaking Bad - S01E01E02.mkv",
        size_bytes=100000,
        mtime=123.0,
        partial_hash="f" * 64,
        container="matroska",
        duration_seconds=5400.0,
        streams_json={"streams": []},
    )
    db_session.add(media_file)
    await db_session.flush()

    # 4. Episode 1 & 2
    ep1_item = MediaItem(
        library_id=library.id,
        type="episode",
        title="Pilot",
        sort_title="pilot",
        year=2008,
    )
    ep1_item.episode = Episode(
        series_id=series_item.id,
        season_id=season_item.id,
        season_number=1,
        episode_number=1,
        air_date="2008-01-20",
        absolute_number=1,
        file_id=media_file.id,
    )
    ep2_item = MediaItem(
        library_id=library.id,
        type="episode",
        title="Cat's in the Bag...",
        sort_title="cats in the bag",
        year=2008,
    )
    ep2_item.episode = Episode(
        series_id=series_item.id,
        season_id=season_item.id,
        season_number=1,
        episode_number=2,
        air_date="2008-01-27",
        absolute_number=2,
        file_id=media_file.id,
    )
    # 5. Unknown episode (no season)
    unknown_item = MediaItem(
        library_id=library.id,
        type="episode",
        title="Unknown Special",
        sort_title="unknown special",
    )
    unknown_item.episode = Episode(
        series_id=series_item.id,
        season_id=None,
        season_number=None,
        episode_number=None,
    )
    db_session.add_all([ep1_item, ep2_item, unknown_item])
    await db_session.commit()
    db_session.expunge_all()

    # Query and assert relationships
    loaded_series = await db_session.scalar(select(MediaItem).where(MediaItem.id == series_item.id))
    assert loaded_series is not None
    assert loaded_series.series is not None
    assert loaded_series.series.status == "Ended"
    assert len(loaded_series.series.seasons) == 1
    assert loaded_series.series.seasons[0].season_number == 1
    assert len(loaded_series.series.episodes) == 3

    loaded_season = await db_session.scalar(select(MediaItem).where(MediaItem.id == season_item.id))
    assert loaded_season is not None
    assert loaded_season.season is not None
    assert loaded_season.season.series.item.title == "Breaking Bad"
    assert len(loaded_season.season.episodes) == 2

    loaded_ep1 = await db_session.scalar(select(MediaItem).where(MediaItem.id == ep1_item.id))
    assert loaded_ep1 is not None
    assert loaded_ep1.episode is not None
    assert loaded_ep1.episode.file is not None
    assert loaded_ep1.episode.file.path.endswith("Breaking Bad - S01E01E02.mkv")
    assert len(loaded_ep1.episode.file.episodes) == 2

    # Delete media file: file_id on episode is set to NULL
    file_to_delete = await db_session.get(MediaFile, media_file.id)
    assert file_to_delete is not None
    await db_session.delete(file_to_delete)
    await db_session.commit()
    db_session.expunge_all()

    refreshed_ep1 = await db_session.scalar(select(MediaItem).where(MediaItem.id == ep1_item.id))
    assert refreshed_ep1 is not None
    assert refreshed_ep1.episode is not None
    assert refreshed_ep1.episode.file_id is None
    assert refreshed_ep1.episode.file is None

    # Deleting series cascades to seasons and episodes
    series_to_delete = await db_session.scalar(
        select(MediaItem).where(MediaItem.id == series_item.id)
    )
    assert series_to_delete is not None
    await db_session.delete(series_to_delete)
    await db_session.commit()

    assert await db_session.scalar(select(func.count()).select_from(Series)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Season)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Episode)) == 0


async def test_music_and_playlist_models(db_session: AsyncSession) -> None:
    user = User(username="musicfan", password_hash="hash")
    library = Library(name="Music Library", type="music")
    library.paths.append(LibraryPath(path="/media/music"))
    db_session.add_all([user, library])
    await db_session.flush()

    # Create Artist
    artist_item = MediaItem(
        library_id=library.id,
        type="artist",
        title="Daft Punk",
        sort_title="daft punk",
    )
    artist_item.artist = Artist(
        musicbrainz_id="056e4f3e-d505-4dad-8ec1-d04f521cbb56",
        country="FR",
        sort_name="Daft Punk",
        disambiguation="French electronic duo",
    )
    db_session.add(artist_item)
    await db_session.flush()

    # Create Album
    album_item = MediaItem(
        library_id=library.id,
        type="album",
        title="Discovery",
        sort_title="discovery",
        year=2001,
    )
    album_item.album = Album(
        artist_id=artist_item.id,
        album_artist="Daft Punk",
        musicbrainz_id="b714b648-936d-3e6f-808b-ec513c1c9ff5",
        musicbrainz_release_group_id="4b245e12-c74d-3759-b1d5-bc44a86f9984",
        release_date="2001-03-12",
        total_discs=1,
        total_tracks=14,
    )
    db_session.add(album_item)
    await db_session.flush()

    # Create MediaFile and Track
    media_file = MediaFile(
        item_id=album_item.id,  # Points to album or track
        path="/media/music/Daft Punk/Discovery/01 - One More Time.flac",
        size_bytes=35000000,
        mtime=1700000000.0,
        partial_hash="f" * 64,
        container="flac",
        duration_seconds=320.5,
        bit_rate=880000,
        streams_json={"streams": [{"codec_type": "audio", "sample_rate": 44100, "channels": 2}]},
    )
    db_session.add(media_file)
    await db_session.flush()

    track_item = MediaItem(
        library_id=library.id,
        type="track",
        title="One More Time",
        sort_title="one more time",
        year=2001,
        runtime_seconds=321,
    )
    track_item.track = Track(
        album_id=album_item.id,
        artist_id=artist_item.id,
        file_id=media_file.id,
        disc_number=1,
        track_number=1,
        duration_seconds=320.5,
        bitrate=880000,
        sample_rate=44100,
        channels=2,
        replaygain_track_gain=-6.5,
        replaygain_track_peak=0.98,
        replaygain_album_gain=-7.1,
        replaygain_album_peak=0.99,
        musicbrainz_track_id="00000000-0000-0000-0000-000000000001",
    )
    db_session.add(track_item)
    await db_session.flush()

    # User Playlist with Track
    playlist = Playlist(
        user_id=user.id,
        name="Electronic Hits",
        description="Best electronic dance tracks",
    )
    playlist.items.append(
        PlaylistItem(
            item_id=track_item.id,
            position=1,
        )
    )
    db_session.add(playlist)
    await db_session.commit()
    db_session.expunge_all()

    # Assert relations
    loaded_artist = await db_session.scalar(select(MediaItem).where(MediaItem.id == artist_item.id))
    assert loaded_artist is not None
    assert loaded_artist.artist is not None
    assert loaded_artist.artist.country == "FR"
    assert len(loaded_artist.artist.albums) == 1
    assert loaded_artist.artist.albums[0].item.title == "Discovery"

    loaded_album = await db_session.scalar(select(MediaItem).where(MediaItem.id == album_item.id))
    assert loaded_album is not None
    assert loaded_album.album is not None
    assert loaded_album.album.artist is not None
    assert loaded_album.album.artist.item.title == "Daft Punk"
    assert len(loaded_album.album.tracks) == 1
    assert loaded_album.album.tracks[0].track_number == 1
    assert loaded_album.album.tracks[0].replaygain_track_gain == -6.5
    assert loaded_album.album.tracks[0].file is not None
    assert loaded_album.album.tracks[0].file.container == "flac"

    loaded_playlist = await db_session.scalar(select(Playlist).where(Playlist.id == playlist.id))
    assert loaded_playlist is not None
    assert loaded_playlist.name == "Electronic Hits"
    assert loaded_playlist.user.username == "musicfan"
    assert len(loaded_playlist.items) == 1
    assert loaded_playlist.items[0].item.title == "One More Time"

    # Deleting MediaFile sets file_id to NULL on track
    loaded_file = await db_session.get(MediaFile, media_file.id)
    assert loaded_file is not None
    await db_session.delete(loaded_file)
    await db_session.commit()
    db_session.expunge_all()

    reloaded_track = await db_session.scalar(select(Track).where(Track.item_id == track_item.id))
    assert reloaded_track is not None
    assert reloaded_track.file_id is None
    assert reloaded_track.file is None

    # Deleting album cascades to track
    album_to_delete = await db_session.scalar(
        select(MediaItem).where(MediaItem.id == album_item.id)
    )
    assert album_to_delete is not None
    await db_session.delete(album_to_delete)
    await db_session.commit()

    assert await db_session.scalar(select(func.count()).select_from(Album)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Track)) == 0
    assert await db_session.scalar(select(func.count()).select_from(Artist)) == 1
