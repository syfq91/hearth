"""Unit tests for mutagen audio tag reading and path fallbacks."""

import struct
from pathlib import Path

from mutagen.apev2 import APEv2
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
    UFID,
)
from mutagen.mp4 import MP4, MP4Cover

from app.services.media.tags import read_audio_tags


def _create_mp3(path: Path) -> None:
    # 5 MPEG 1 Layer 3 frames (128 kbps, 44.1 kHz, stereo)
    frame = b"\xff\xfb\x90\x00" + (b"\x00" * 413)
    path.write_bytes(frame * 5)


def _create_flac(path: Path) -> None:
    header = b"fLaC\x80\x00\x00\x22"
    streaminfo = struct.pack(">HH", 4096, 4096) + b"\x00\x00\x00\x00\x00\x00"
    sr_chan_bps_samples = (44100 << 44) | (1 << 41) | (15 << 36) | 44100
    streaminfo += sr_chan_bps_samples.to_bytes(8, "big") + (b"\x00" * 16)
    path.write_bytes(header + streaminfo)


def _create_m4a(path: Path) -> None:
    ftyp = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A mp42isom\x00\x00\x00\x00"
    moov = b"\x00\x00\x00\x08moov"
    path.write_bytes(ftyp + moov)


def _create_ape(path: Path) -> None:
    header = b"MAC \x96\x0f\xe8\x03\x00\x00\x02\x00\x44\xac\x00\x00" + (b"\x00" * 50)
    path.write_bytes(header)


def test_id3_tag_reading_and_embedded_cover(tmp_path: Path) -> None:
    mp3_file = tmp_path / "song.mp3"
    _create_mp3(mp3_file)

    tag = ID3()
    tag.add(TIT2(encoding=3, text=["Digital Love"]))
    tag.add(TPE1(encoding=3, text=["Daft Punk"]))
    tag.add(TPE2(encoding=3, text=["Daft Punk"]))
    tag.add(TALB(encoding=3, text=["Discovery"]))
    tag.add(TRCK(encoding=3, text=["3/14"]))
    tag.add(TPOS(encoding=3, text=["1/1"]))
    tag.add(TDRC(encoding=3, text=["2001-03-12"]))
    tag.add(TCON(encoding=3, text=["Electronic / House"]))
    tag.add(TCMP(encoding=3, text=["0"]))
    tag.add(UFID(owner="http://musicbrainz.org", data=b"mb-track-uuid-1234"))
    tag.add(TXXX(encoding=3, desc="MusicBrainz Release Track Id", text=["mb-rel-track-123"]))
    tag.add(TXXX(encoding=3, desc="MusicBrainz Artist Id", text=["mb-artist-123"]))
    tag.add(TXXX(encoding=3, desc="MusicBrainz Album Id", text=["mb-album-123"]))
    tag.add(TXXX(encoding=3, desc="MusicBrainz Album Artist Id", text=["mb-albumartist-123"]))
    tag.add(TXXX(encoding=3, desc="MusicBrainz Release Group Id", text=["mb-rg-123"]))
    tag.add(TXXX(encoding=3, desc="replaygain_track_gain", text=["-6.50 dB"]))
    tag.add(TXXX(encoding=3, desc="replaygain_track_peak", text=["0.985000"]))
    tag.add(TXXX(encoding=3, desc="replaygain_album_gain", text=["-7.10 dB"]))
    tag.add(TXXX(encoding=3, desc="replaygain_album_peak", text=["0.992000"]))
    tag.add(
        APIC(
            encoding=3,
            mime="image/jpeg",
            type=3,
            desc="Front Cover",
            data=b"\xff\xd8\xff\xe0mock_cover",
        )
    )
    tag.save(str(mp3_file))

    tags = read_audio_tags(mp3_file)
    assert tags.container == "mp3"
    assert tags.title == "Digital Love"
    assert tags.artist == "Daft Punk"
    assert tags.album_artist == "Daft Punk"
    assert tags.album == "Discovery"
    assert tags.track_number == 3
    assert tags.total_tracks == 14
    assert tags.disc_number == 1
    assert tags.total_discs == 1
    assert tags.year == 2001
    assert tags.release_date == "2001-03-12"
    assert "Electronic" in tags.genres and "House" in tags.genres
    assert tags.compilation is False
    assert tags.musicbrainz_track_id == "mb-track-uuid-1234"
    assert tags.musicbrainz_release_track_id == "mb-rel-track-123"
    assert tags.musicbrainz_artist_id == "mb-artist-123"
    assert tags.musicbrainz_album_id == "mb-album-123"
    assert tags.musicbrainz_album_artist_id == "mb-albumartist-123"
    assert tags.musicbrainz_release_group_id == "mb-rg-123"
    assert tags.replaygain_track_gain == -6.5
    assert tags.replaygain_track_peak == 0.985
    assert tags.replaygain_album_gain == -7.1
    assert tags.replaygain_album_peak == 0.992
    assert tags.embedded_cover is not None
    assert tags.embedded_cover.data == b"\xff\xd8\xff\xe0mock_cover"
    assert tags.embedded_cover.mime_type == "image/jpeg"
    assert tags.duration_seconds is not None and tags.duration_seconds > 0


def test_vorbis_tag_reading_from_flac(tmp_path: Path) -> None:
    flac_file = tmp_path / "song.flac"
    _create_flac(flac_file)

    audio = FLAC(str(flac_file))
    audio["title"] = "Teardrop"
    audio["artist"] = "Massive Attack feat. Elizabeth Fraser"
    audio["albumartist"] = "Massive Attack"
    audio["album"] = "Mezzanine"
    audio["tracknumber"] = "3"
    audio["totaltracks"] = "11"
    audio["discnumber"] = "1"
    audio["totaldiscs"] = "1"
    audio["date"] = "1998-04-20"
    audio["genre"] = "Trip Hop; Electronic"
    audio["compilation"] = "0"
    audio["musicbrainz_trackid"] = "mb-flac-track-1"
    audio["replaygain_track_gain"] = "-5.40 dB"
    audio["replaygain_track_peak"] = "0.990"

    pic = Picture()
    pic.type = 3
    pic.mime = "image/png"
    pic.desc = "Album Front"
    pic.data = b"\x89PNGmock_png_cover"
    audio.add_picture(pic)
    audio.save()

    tags = read_audio_tags(flac_file)
    assert tags.container == "flac"
    assert tags.title == "Teardrop"
    assert tags.artist == "Massive Attack feat. Elizabeth Fraser"
    assert tags.album_artist == "Massive Attack"
    assert tags.album == "Mezzanine"
    assert tags.track_number == 3
    assert tags.total_tracks == 11
    assert tags.disc_number == 1
    assert tags.year == 1998
    assert tags.release_date == "1998-04-20"
    assert tags.musicbrainz_track_id == "mb-flac-track-1"
    assert tags.replaygain_track_gain == -5.4
    assert tags.replaygain_track_peak == 0.99
    assert tags.embedded_cover is not None
    assert tags.embedded_cover.mime_type == "image/png"
    assert tags.embedded_cover.data == b"\x89PNGmock_png_cover"


def test_mp4_tag_reading_from_m4a(tmp_path: Path) -> None:
    m4a_file = tmp_path / "song.m4a"
    _create_m4a(m4a_file)

    audio = MP4(str(m4a_file))
    audio["\xa9nam"] = ["Get Lucky"]
    audio["\xa9ART"] = ["Daft Punk feat. Pharrell Williams"]
    audio["aART"] = ["Daft Punk"]
    audio["\xa9alb"] = ["Random Access Memories"]
    audio["trkn"] = [(8, 13)]
    audio["disk"] = [(1, 1)]
    audio["\xa9day"] = ["2013-05-17"]
    audio["\xa9gen"] = ["Disco, Funk"]
    audio["cpil"] = False
    audio["----:com.apple.iTunes:MusicBrainz Track Id"] = [b"mb-m4a-track-8"]
    audio["----:com.apple.iTunes:replaygain_track_gain"] = [b"-8.2 dB"]
    audio["covr"] = [MP4Cover(b"\xff\xd8\xff\xe0jpegcover", imageformat=MP4Cover.FORMAT_JPEG)]
    audio.save()

    tags = read_audio_tags(m4a_file)
    assert tags.container == "m4a"
    assert tags.title == "Get Lucky"
    assert tags.artist == "Daft Punk feat. Pharrell Williams"
    assert tags.album_artist == "Daft Punk"
    assert tags.album == "Random Access Memories"
    assert tags.track_number == 8
    assert tags.total_tracks == 13
    assert tags.disc_number == 1
    assert tags.year == 2013
    assert tags.musicbrainz_track_id == "mb-m4a-track-8"
    assert tags.replaygain_track_gain == -8.2
    assert tags.embedded_cover is not None
    assert tags.embedded_cover.mime_type == "image/jpeg"
    assert tags.embedded_cover.data == b"\xff\xd8\xff\xe0jpegcover"


def test_ape_tag_reading(tmp_path: Path) -> None:
    ape_file = tmp_path / "song.ape"
    _create_ape(ape_file)

    try:
        tag = APEv2(str(ape_file))
    except Exception:
        tag = APEv2()
    tag["Title"] = "Time"
    tag["Artist"] = "Pink Floyd"
    tag["Album Artist"] = "Pink Floyd"
    tag["Album"] = "The Dark Side of the Moon"
    tag["Track"] = "4/10"
    tag["Disc"] = "1/1"
    tag["Year"] = "1973"
    tag["Genre"] = "Progressive Rock"
    tag["Cover Art (Front)"] = b"Cover\x00\xff\xd8\xff\xe0apejpeg"
    tag.save(str(ape_file))

    tags = read_audio_tags(ape_file)
    assert tags.container == "ape"
    assert tags.title == "Time"
    assert tags.artist == "Pink Floyd"
    assert tags.album == "The Dark Side of the Moon"
    assert tags.track_number == 4
    assert tags.total_tracks == 10
    assert tags.year == 1973
    assert tags.embedded_cover is not None
    assert tags.embedded_cover.mime_type == "image/jpeg"
    assert tags.embedded_cover.data == b"\xff\xd8\xff\xe0apejpeg"


def test_path_fallback_resolution(tmp_path: Path) -> None:
    # 1. Standard Artist / Album / Track structure with empty tags
    track_path = tmp_path / "Queen" / "A Night at the Opera (1975)" / "04 - Bohemian Rhapsody.flac"
    track_path.parent.mkdir(parents=True)
    _create_flac(track_path)

    tags = read_audio_tags(track_path, library_root=tmp_path)
    assert tags.title == "Bohemian Rhapsody"
    assert tags.track_number == 4
    assert tags.album == "A Night at the Opera"
    assert tags.year == 1975
    assert tags.artist == "Queen"
    assert tags.album_artist == "Queen"

    # 2. Multi-disc album folder: Artist / Album / Disc 2 / 03 - Song.mp3
    disc2_track = tmp_path / "Pink Floyd" / "The Wall" / "Disc 2" / "03 - Comfortably Numb.mp3"
    disc2_track.parent.mkdir(parents=True)
    _create_mp3(disc2_track)

    tags2 = read_audio_tags(disc2_track, library_root=tmp_path)
    assert tags2.title == "Comfortably Numb"
    assert tags2.track_number == 3
    assert tags2.disc_number == 2
    assert tags2.album == "The Wall"
    assert tags2.artist == "Pink Floyd"
    assert tags2.album_artist == "Pink Floyd"

    # 3. Compilation album with "Various Artists"
    comp_track = tmp_path / "Various Artists" / "Pulp Fiction OST" / "01 - Dick Dale - Misirlou.mp3"
    comp_track.parent.mkdir(parents=True)
    _create_mp3(comp_track)

    tags3 = read_audio_tags(comp_track, library_root=tmp_path)
    assert tags3.title == "Misirlou"
    assert tags3.track_number == 1
    assert tags3.artist == "Dick Dale"
    assert tags3.album_artist == "Various Artists"
    assert tags3.album == "Pulp Fiction OST"


def test_tags_priority_over_folder_hints(tmp_path: Path) -> None:
    # Tags say "Real Artist" and "Real Album", folder says "Messy Folder"
    track_path = tmp_path / "Unknown" / "Random Album" / "track01.mp3"
    track_path.parent.mkdir(parents=True)
    _create_mp3(track_path)

    tag = ID3()
    tag.add(TIT2(encoding=3, text=["Around the World"]))
    tag.add(TPE1(encoding=3, text=["Daft Punk"]))
    tag.add(TPE2(encoding=3, text=["Daft Punk"]))
    tag.add(TALB(encoding=3, text=["Homework"]))
    tag.add(TRCK(encoding=3, text=["7"]))
    tag.save(str(track_path))

    tags = read_audio_tags(track_path, library_root=tmp_path)
    assert tags.title == "Around the World"
    assert tags.artist == "Daft Punk"
    assert tags.album_artist == "Daft Punk"
    assert tags.album == "Homework"
    assert tags.track_number == 7
