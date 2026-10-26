"""Integration tests for music library scanning, tagging, and catalog hierarchy."""

import struct
from datetime import UTC
from pathlib import Path

import pytest
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,
    ID3,
    TALB,
    TCMP,
    TCON,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TPOS,
    TRCK,
    TXXX,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Album, Library, LibraryPath, MediaFile, MediaItem, Track
from app.services.images.pipeline import ImagePipeline
from app.services.metadata.matching import MetadataMatcher
from app.services.scanner.scanner import LibraryScanner


def _make_mp3(path: Path) -> None:
    frame = b"\xff\xfb\x90\x00" + (b"\x00" * 413)
    path.write_bytes(frame * 5)


def _make_flac(path: Path) -> None:
    header = b"fLaC\x80\x00\x00\x22"
    streaminfo = struct.pack(">HH", 4096, 4096) + b"\x00\x00\x00\x00\x00\x00"
    sr_chan_bps_samples = (44100 << 44) | (1 << 41) | (15 << 36) | 44100
    streaminfo += sr_chan_bps_samples.to_bytes(8, "big") + (b"\x00" * 16)
    path.write_bytes(header + streaminfo)


@pytest.fixture
def fake_jpeg() -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
        b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12"
        b"\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x02\x00\x02\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00?"
        b"\x00\xbf\x80\xff\xd9"
    )


async def test_music_library_scan_hierarchy_and_tags(
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
    tmp_path: Path,
    fake_jpeg: bytes,
) -> None:
    # 1. Setup music directory
    music_dir = tmp_path / "music"
    music_dir.mkdir()

    # Artist 1: Daft Punk / Discovery (2001) / 2 tracks with ID3 tags
    album1_dir = music_dir / "Daft Punk" / "Discovery"
    album1_dir.mkdir(parents=True)

    t1_path = album1_dir / "01 - One More Time.mp3"
    _make_mp3(t1_path)
    id3_1 = ID3()
    id3_1.add(TIT2(encoding=3, text="One More Time"))
    id3_1.add(TPE1(encoding=3, text="Daft Punk"))
    id3_1.add(TPE2(encoding=3, text="Daft Punk"))
    id3_1.add(TALB(encoding=3, text="Discovery"))
    id3_1.add(TDRC(encoding=3, text="2001"))
    id3_1.add(TRCK(encoding=3, text="1/14"))
    id3_1.add(TPOS(encoding=3, text="1/1"))
    id3_1.add(TCON(encoding=3, text="Electronic"))
    id3_1.add(TXXX(encoding=3, desc="replaygain_track_gain", text="-4.2 dB"))
    id3_1.add(TXXX(encoding=3, desc="replaygain_track_peak", text="0.95"))
    id3_1.add(
        APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,
            desc="Front Cover",
            data=fake_jpeg,
        )
    )
    id3_1.save(str(t1_path))

    t2_path = album1_dir / "02 - Aerodynamic.mp3"
    _make_mp3(t2_path)
    id3_2 = ID3()
    id3_2.add(TIT2(encoding=3, text="Aerodynamic"))
    id3_2.add(TPE1(encoding=3, text="Daft Punk"))
    id3_2.add(TPE2(encoding=3, text="Daft Punk"))
    id3_2.add(TALB(encoding=3, text="Discovery"))
    id3_2.add(TDRC(encoding=3, text="2001"))
    id3_2.add(TRCK(encoding=3, text="2/14"))
    id3_2.add(TPOS(encoding=3, text="1/1"))
    id3_2.add(TCON(encoding=3, text="Electronic"))
    id3_2.save(str(t2_path))

    # Artist 2: Pink Floyd / The Wall (Multi-disc)
    pf_disc1 = music_dir / "Pink Floyd" / "The Wall" / "Disc 1"
    pf_disc2 = music_dir / "Pink Floyd" / "The Wall" / "Disc 2"
    pf_disc1.mkdir(parents=True)
    pf_disc2.mkdir(parents=True)

    d1_t1 = pf_disc1 / "01 - In the Flesh.flac"
    _make_flac(d1_t1)
    flac1 = FLAC(str(d1_t1))
    flac1["title"] = "In the Flesh?"
    flac1["artist"] = "Pink Floyd"
    flac1["album"] = "The Wall"
    flac1["albumartist"] = "Pink Floyd"
    flac1["date"] = "1979"
    flac1["discnumber"] = "1/2"
    flac1["tracknumber"] = "1/13"
    pic = Picture()
    pic.type = 3
    pic.mime = "image/jpeg"
    pic.data = fake_jpeg
    flac1.add_picture(pic)
    flac1.save()

    d2_t1 = pf_disc2 / "01 - Hey You.flac"
    _make_flac(d2_t1)
    flac2 = FLAC(str(d2_t1))
    flac2["title"] = "Hey You"
    flac2["artist"] = "Pink Floyd"
    flac2["album"] = "The Wall"
    flac2["albumartist"] = "Pink Floyd"
    flac2["date"] = "1979"
    flac2["discnumber"] = "2/2"
    flac2["tracknumber"] = "1/13"
    flac2.save()

    # Artist 3: Various Artists / Pulp Fiction OST (Compilation)
    comp_dir = music_dir / "Various Artists" / "Pulp Fiction OST"
    comp_dir.mkdir(parents=True)
    comp_t1 = comp_dir / "01 - Dick Dale - Misirlou.mp3"
    _make_mp3(comp_t1)
    id3_c1 = ID3()
    id3_c1.add(TIT2(encoding=3, text="Misirlou"))
    id3_c1.add(TPE1(encoding=3, text="Dick Dale"))
    id3_c1.add(TPE2(encoding=3, text="Various Artists"))
    id3_c1.add(TALB(encoding=3, text="Pulp Fiction OST"))
    id3_c1.add(TCMP(encoding=3, text="1"))
    id3_c1.add(TRCK(encoding=3, text="1"))
    id3_c1.save(str(comp_t1))

    # Add local cover.jpg for the compilation
    (comp_dir / "cover.jpg").write_bytes(fake_jpeg)

    # 2. Create Library row in database
    library_id = "lib-music-1"
    async with db_sessionmaker() as session:
        lib = Library(id=library_id, name="Music Library", type="music")
        lib.paths.append(LibraryPath(id="lp-1", library_id=library_id, path=str(music_dir)))
        session.add(lib)
        await session.commit()

    # 3. Run LibraryScanner
    image_pipeline = ImagePipeline(settings=db_settings, sessionmaker=db_sessionmaker)
    matcher = MetadataMatcher(
        db_settings,
        db_sessionmaker,
        providers=[],
        artwork=image_pipeline,
    )
    scanner = LibraryScanner(
        db_settings,
        db_sessionmaker,
        matcher=matcher,
    )

    stats = await scanner.scan_library(library_id)
    assert stats.added == 5
    assert stats.missing == 0
    assert stats.removed == 0

    # 4. Assert Database hierarchy and metadata
    async with db_sessionmaker() as session:
        # Artists
        artists = (await session.scalars(select(MediaItem).where(MediaItem.type == "artist"))).all()
        artist_names = {a.title for a in artists}
        assert "Daft Punk" in artist_names
        assert "Pink Floyd" in artist_names
        assert "Various Artists" in artist_names
        assert "Dick Dale" in artist_names  # Track artist on compilation

        # Albums
        albums = (await session.scalars(select(MediaItem).where(MediaItem.type == "album"))).all()
        album_titles = {a.title for a in albums}
        assert "Discovery" in album_titles
        assert "The Wall" in album_titles
        assert "Pulp Fiction OST" in album_titles

        # Check "The Wall" has 2 tracks on different discs
        the_wall_item = next(a for a in albums if a.title == "The Wall")
        wall_album = await session.get(Album, the_wall_item.id)
        assert wall_album is not None

        wall_tracks = (
            await session.scalars(select(Track).where(Track.album_id == the_wall_item.id))
        ).all()
        assert len(wall_tracks) == 2
        discs = {t.disc_number for t in wall_tracks}
        assert discs == {1, 2}

        # Check Daft Punk track tags and ReplayGain
        dp_item = next(a for a in albums if a.title == "Discovery")
        dp_tracks = (
            await session.scalars(
                select(Track).where(Track.album_id == dp_item.id).order_by(Track.track_number)
            )
        ).all()
        assert len(dp_tracks) == 2
        t1 = dp_tracks[0]
        assert t1.track_number == 1
        assert t1.disc_number == 1
        assert t1.replaygain_track_gain == -4.2
        assert t1.replaygain_track_peak == 0.95

        # Check track media file
        file_row = await session.get(MediaFile, t1.file_id)
        assert file_row is not None
        assert file_row.container == "mp3"
        assert file_row.streams_json["codec_name"] == "mp3"

    # 5. Rescan with no changes -> nothing added, nothing removed
    stats_rescan = await scanner.scan_library(library_id)
    assert stats_rescan.added == 0
    assert stats_rescan.missing == 0
    assert stats_rescan.removed == 0

    # 6. Delete a track file and rescan
    t2_path.unlink()
    stats_delete = await scanner.scan_library(library_id)
    assert stats_delete.missing == 1

    # 7. Manually age missing_since beyond grace period and rescan
    from datetime import datetime, timedelta

    async with db_sessionmaker() as session:
        file_row = await session.scalar(select(MediaFile).where(MediaFile.path == str(t2_path)))
        assert file_row is not None
        assert file_row.missing_since is not None
        file_row.missing_since = datetime.now(UTC) - timedelta(
            hours=db_settings.scan_missing_grace_hours + 1
        )
        await session.commit()

    stats_purge = await scanner.scan_library(library_id)
    assert stats_purge.removed == 1

    # Verify track 2 and its file are deleted
    async with db_sessionmaker() as session:
        file_row = await session.scalar(select(MediaFile).where(MediaFile.path == str(t2_path)))
        assert file_row is None

    # 8. Delete the remaining Daft Punk track -> album and artist should cascade delete
    t1_path.unlink()
    await scanner.scan_library(library_id)
    async with db_sessionmaker() as session:
        file_row = await session.scalar(select(MediaFile).where(MediaFile.path == str(t1_path)))
        assert file_row is not None
        file_row.missing_since = datetime.now(UTC) - timedelta(
            hours=db_settings.scan_missing_grace_hours + 1
        )
        await session.commit()

    stats_purge_all = await scanner.scan_library(library_id)
    assert stats_purge_all.removed == 1

    async with db_sessionmaker() as session:
        # Discovery album should be gone
        dp_album = await session.scalar(
            select(MediaItem).where(MediaItem.type == "album", MediaItem.title == "Discovery")
        )
        assert dp_album is None
        # Daft Punk artist should be gone
        dp_artist = await session.scalar(
            select(MediaItem).where(MediaItem.type == "artist", MediaItem.title == "Daft Punk")
        )
        assert dp_artist is None
