"""Integration tests for music metadata enrichment.

Tests MusicBrainz, Cover Art Archive, and local images.
"""

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import (
    Album,
    Artist,
    Genre,
    Image,
    ItemGenre,
    Library,
    MediaFile,
    MediaItem,
    Track,
)
from app.services.images.pipeline import ImagePipeline
from app.services.metadata.matching import MetadataMatcher
from app.services.metadata.musicbrainz import MusicBrainzProvider

MB_ARTIST_DETAILS = {
    "id": "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d",
    "name": "The Beatles",
    "sort-name": "Beatles, The",
    "type": "Group",
    "country": "GB",
    "disambiguation": "legendary British rock band",
    "annotation": {"text": "The Beatles were an English rock band formed in Liverpool."},
    "genres": [{"name": "rock"}, {"name": "pop rock"}],
}

MB_RELEASE_DETAILS = {
    "id": "a6da631a-e4cc-4299-8ab4-3ab833630f9d",
    "title": "Abbey Road",
    "date": "1969-09-26",
    "artist-credit": [
        {
            "name": "The Beatles",
            "artist": {"id": "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d", "name": "The Beatles"},
        }
    ],
    "release-group": {
        "id": "f5093c06-23e3-404f-aeaa-40f720264821",
        "genres": [{"name": "rock"}],
    },
    "media": [{"disc-count": 1, "track-count": 17, "format": "CD"}],
    "genres": [{"name": "rock"}],
}


@pytest.fixture
def fake_jpeg() -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
        b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12"
        b"\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x02\x00\x02\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00?"
        b"\x00\xbf\x80\xff\xd9"
    )


async def test_artist_enrichment_and_local_image_discovery(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
    tmp_path: Path,
    fake_jpeg: bytes,
) -> None:
    # Setup folders with local artist.jpg
    music_dir = tmp_path / "music"
    artist_dir = music_dir / "The Beatles"
    album_dir = artist_dir / "Abbey Road"
    album_dir.mkdir(parents=True)

    # Local artist image in artist folder
    (artist_dir / "artist.jpg").write_bytes(fake_jpeg)

    # Track audio file
    track_file = album_dir / "01 - Come Together.mp3"
    track_file.write_bytes(b"\x00" * 100)

    # Create DB rows: Library, Artist, Album, Track, MediaFile
    library_id = "lib-m-1"
    artist_id = "artist-1"
    album_id = "album-1"
    track_id = "track-1"
    file_id = "file-1"

    async with db_sessionmaker() as session:
        lib = Library(id=library_id, name="Music", type="music")
        session.add(lib)

        artist_item = MediaItem(
            id=artist_id,
            library_id=library_id,
            type="artist",
            title="The Beatles",
            sort_title="Beatles, The",
            match_state="pending",
        )
        artist_item.artist = Artist(
            item_id=artist_id,
            musicbrainz_id="b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d",
        )
        session.add(artist_item)

        album_item = MediaItem(
            id=album_id,
            library_id=library_id,
            type="album",
            title="Abbey Road",
            sort_title="Abbey Road",
            match_state="pending",
        )
        album_item.album = Album(
            item_id=album_id,
            artist_id=artist_id,
            album_artist="The Beatles",
        )
        session.add(album_item)

        track_item = MediaItem(
            id=track_id,
            library_id=library_id,
            type="track",
            title="Come Together",
            sort_title="Come Together",
            match_state="matched",
        )
        track_item.track = Track(
            item_id=track_id,
            album_id=album_id,
            artist_id=artist_id,
            file_id=file_id,
            track_number=1,
            disc_number=1,
        )
        session.add(track_item)

        mf = MediaFile(
            id=file_id,
            item_id=track_id,
            path=str(track_file),
            size_bytes=100,
            mtime=1.0,
            partial_hash="fake",
            container="mp3",
        )
        session.add(mf)
        await session.commit()

    # Mock MusicBrainz transport
    def mb_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "artist/b10bbbfc" in url_str:
            return httpx.Response(200, json=MB_ARTIST_DETAILS)
        return httpx.Response(404)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(mb_handler),
        base_url="https://musicbrainz.org/ws/2",
    )
    provider = MusicBrainzProvider(db_settings, db_sessionmaker, client=client)

    pipeline = ImagePipeline(settings=db_settings, sessionmaker=db_sessionmaker)
    matcher = MetadataMatcher(
        db_settings,
        db_sessionmaker,
        providers=[provider],
        artwork=pipeline,
    )

    try:
        result = await matcher.match_item(artist_id)
        assert result.state == "matched"
        assert result.provider == "musicbrainz"

        # Check DB updates
        async with db_sessionmaker() as session:
            refreshed = await session.get(MediaItem, artist_id)
            assert refreshed is not None
            assert refreshed.match_state == "matched"
            assert "English rock band" in (refreshed.overview or "")
            assert refreshed.artist is not None
            assert refreshed.artist.country == "GB"
            assert refreshed.artist.disambiguation == "legendary British rock band"

            # Check genres
            genres = list(
                await session.scalars(
                    select(Genre.name)
                    .join(ItemGenre, ItemGenre.genre_id == Genre.id)
                    .where(ItemGenre.item_id == artist_id)
                )
            )
            assert "Rock" in genres or "Pop Rock" in genres

            # Check local artwork was stored in Image table
            art_images = list(
                await session.scalars(select(Image).where(Image.item_id == artist_id))
            )
            assert len(art_images) >= 1
            assert art_images[0].kind == "poster"
    finally:
        await client.aclose()


async def test_album_enrichment_and_cover_art_archive(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
    tmp_path: Path,
    fake_jpeg: bytes,
) -> None:
    library_id = "lib-m-2"
    artist_id = "artist-2"
    album_id = "album-2"

    async with db_sessionmaker() as session:
        lib = Library(id=library_id, name="Music", type="music")
        session.add(lib)

        artist_item = MediaItem(
            id=artist_id,
            library_id=library_id,
            type="artist",
            title="The Beatles",
            sort_title="Beatles, The",
            match_state="matched",
        )
        artist_item.artist = Artist(item_id=artist_id)
        session.add(artist_item)

        album_item = MediaItem(
            id=album_id,
            library_id=library_id,
            type="album",
            title="Abbey Road",
            sort_title="Abbey Road",
            match_state="pending",
        )
        album_item.album = Album(
            item_id=album_id,
            artist_id=artist_id,
            album_artist="The Beatles",
            musicbrainz_id="a6da631a-e4cc-4299-8ab4-3ab833630f9d",
        )
        session.add(album_item)
        await session.commit()

    def mb_handler(request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)
        if "release/a6da631a" in url_str:
            return httpx.Response(200, json=MB_RELEASE_DETAILS)
        return httpx.Response(404)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(mb_handler),
        base_url="https://musicbrainz.org/ws/2",
    )
    provider = MusicBrainzProvider(db_settings, db_sessionmaker, client=client)

    # Mock ImagePipeline download to serve fake_jpeg for coverartarchive
    def caa_handler(request: httpx.Request) -> httpx.Response:
        if "coverartarchive.org" in str(request.url):
            return httpx.Response(200, content=fake_jpeg, headers={"Content-Type": "image/jpeg"})
        return httpx.Response(404)

    image_client = httpx.AsyncClient(transport=httpx.MockTransport(caa_handler))
    pipeline = ImagePipeline(
        settings=db_settings,
        sessionmaker=db_sessionmaker,
        client=image_client,
    )

    matcher = MetadataMatcher(
        db_settings,
        db_sessionmaker,
        providers=[provider],
        artwork=pipeline,
    )

    try:
        result = await matcher.match_item(album_id)
        assert result.state == "matched"
        assert result.provider == "musicbrainz"

        # Check DB updates
        async with db_sessionmaker() as session:
            refreshed = await session.get(MediaItem, album_id)
            assert refreshed is not None
            assert refreshed.match_state == "matched"
            assert refreshed.year == 1969
            assert refreshed.album is not None
            assert refreshed.album.release_date == "1969-09-26"
            assert refreshed.album.total_discs == 1
            assert refreshed.album.total_tracks == 17
            rg_id = "f5093c06-23e3-404f-aeaa-40f720264821"
            assert refreshed.album.musicbrainz_release_group_id == rg_id

            # Check cover art was downloaded from CAA and cached
            images = list(await session.scalars(select(Image).where(Image.item_id == album_id)))
            assert len(images) == 1
            assert images[0].kind == "poster"
    finally:
        await client.aclose()
        await image_client.aclose()


async def test_graceful_degradation_on_musicbrainz_failure(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    library_id = "lib-m-3"
    album_id = "album-3"

    async with db_sessionmaker() as session:
        lib = Library(id=library_id, name="Music", type="music")
        session.add(lib)

        album_item = MediaItem(
            id=album_id,
            library_id=library_id,
            type="album",
            title="Offline Album",
            sort_title="Offline Album",
            match_state="pending",
        )
        album_item.album = Album(
            item_id=album_id,
            album_artist="Unknown Artist",
        )
        session.add(album_item)
        await session.commit()

    # Provider that raises an error (e.g. 500 or network failure)
    def fail_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(fail_handler),
        base_url="https://musicbrainz.org/ws/2",
    )
    provider = MusicBrainzProvider(db_settings, db_sessionmaker, client=client)

    matcher = MetadataMatcher(
        db_settings,
        db_sessionmaker,
        providers=[provider],
    )

    try:
        # Should NOT raise, but record retryable state
        result = await matcher.match_item(album_id)
        assert result.state == "pending"

        async with db_sessionmaker() as session:
            item = await session.get(MediaItem, album_id)
            assert item is not None
            assert item.match_state == "pending"
            assert item.match_error is not None
            assert item.title == "Offline Album"  # Catalog entry intact!
    finally:
        await client.aclose()
