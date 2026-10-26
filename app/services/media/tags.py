"""Audio tag reading with mutagen and path fallback resolution."""

from __future__ import annotations

import base64
import contextlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mutagen
from mutagen.apev2 import APEv2
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3
from mutagen.mp4 import MP4Cover, MP4Tags

_DISC_DIR_PATTERN = re.compile(r"^(?:cd|disc|disk)\s*(\d+)$", re.IGNORECASE)
_TRACK_FILE_PATTERN = re.compile(
    r"^(?:(?P<disc>\d+)[-._])?(?P<track>\d{1,3})[\s._-]+(?:(?P<artist>[^–—-]+)\s*[-–—]\s*)?(?P<title>.+)$"
)
_GAIN_PATTERN = re.compile(r"[-+]?[0-9]*\.?[0-9]+")
_YEAR_PATTERN = re.compile(r"(\d{4})")


@dataclass
class EmbeddedCover:
    """Embedded album artwork extracted from audio tags."""

    data: bytes
    mime_type: str = "image/jpeg"
    description: str = ""


@dataclass
class AudioTags:
    """Normalized audio metadata read from tags and file layout."""

    title: str | None = None
    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    disc_number: int = 1
    total_discs: int | None = None
    track_number: int | None = None
    total_tracks: int | None = None
    year: int | None = None
    release_date: str | None = None
    genres: list[str] = field(default_factory=list)
    compilation: bool = False

    # MusicBrainz identifiers
    musicbrainz_track_id: str | None = None
    musicbrainz_release_track_id: str | None = None
    musicbrainz_artist_id: str | None = None
    musicbrainz_album_id: str | None = None
    musicbrainz_album_artist_id: str | None = None
    musicbrainz_release_group_id: str | None = None

    # ReplayGain metadata
    replaygain_track_gain: float | None = None
    replaygain_track_peak: float | None = None
    replaygain_album_gain: float | None = None
    replaygain_album_peak: float | None = None

    # Technical stream properties from mutagen info
    duration_seconds: float | None = None
    bitrate: int | None = None
    sample_rate: int | None = None
    channels: int | None = 2
    container: str | None = None

    # Embedded artwork
    embedded_cover: EmbeddedCover | None = None


def _clean_str(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        if not val:
            return None
        val = val[0]
    if isinstance(val, bytes):
        try:
            val = val.decode("utf-8")
        except UnicodeDecodeError:
            return None
    s = str(val).strip()
    return s if s else None


def _parse_int_pair(val: Any) -> tuple[int | None, int | None]:
    if val is None:
        return None, None
    if isinstance(val, tuple) and len(val) >= 2:
        num = int(val[0]) if val[0] is not None and int(val[0]) > 0 else None
        total = int(val[1]) if val[1] is not None and int(val[1]) > 0 else None
        return num, total
    s = _clean_str(val)
    if not s:
        return None, None
    if "/" in s:
        parts = s.split("/", 1)
        try:
            num = int(parts[0].strip())
        except ValueError:
            num = None
        try:
            total = int(parts[1].strip())
        except ValueError:
            total = None
        return num, total
    try:
        return int(s), None
    except ValueError:
        return None, None


def _parse_float(val: Any) -> float | None:
    s = _clean_str(val)
    if not s:
        return None
    match = _GAIN_PATTERN.search(s)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _parse_year_and_date(val: Any) -> tuple[int | None, str | None]:
    s = _clean_str(val)
    if not s:
        return None, None
    year = None
    year_match = _YEAR_PATTERN.search(s)
    if year_match:
        try:
            year = int(year_match.group(1))
        except ValueError:
            year = None
    # ISO date substring YYYY-MM-DD
    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    release_date = date_match.group(1) if date_match else (str(year) if year else None)
    return year, release_date


def _extract_id3(id3: ID3, tags: AudioTags) -> None:
    # Title
    if "TIT2" in id3:
        tags.title = _clean_str(id3["TIT2"].text)

    # Track artist
    if "TPE1" in id3:
        tags.artist = _clean_str(id3["TPE1"].text)

    # Album artist
    if "TPE2" in id3:
        tags.album_artist = _clean_str(id3["TPE2"].text)

    # Album
    if "TALB" in id3:
        tags.album = _clean_str(id3["TALB"].text)

    # Track number & total
    if "TRCK" in id3:
        track_num, track_total = _parse_int_pair(id3["TRCK"].text)
        tags.track_number = track_num
        tags.total_tracks = track_total

    # Disc number & total
    if "TPOS" in id3:
        disc_num, disc_total = _parse_int_pair(id3["TPOS"].text)
        if disc_num:
            tags.disc_number = disc_num
        tags.total_discs = disc_total

    # Year & release date
    date_val = None
    if "TDRC" in id3:
        date_val = id3["TDRC"].text
    elif "TYER" in id3:
        date_val = id3["TYER"].text
    if date_val:
        tags.year, tags.release_date = _parse_year_and_date(date_val)

    # Genre
    if "TCON" in id3:
        genre_str = _clean_str(id3["TCON"].text)
        if genre_str:
            tags.genres = [g.strip() for g in re.split(r"[/,;]", genre_str) if g.strip()]

    # Compilation
    if "TCMP" in id3:
        cmp_val = _clean_str(id3["TCMP"].text)
        if cmp_val in ("1", "true", "True"):
            tags.compilation = True

    # MusicBrainz IDs
    for key, frame in id3.items():
        if key.startswith("UFID:http://musicbrainz.org") and getattr(frame, "data", None):
            tags.musicbrainz_track_id = _clean_str(frame.data)
        elif key.startswith("TXXX:"):
            desc = (getattr(frame, "desc", "") or "").lower()
            text = _clean_str(getattr(frame, "text", None))
            if not text:
                continue
            if desc in ("musicbrainz track id", "musicbrainz_trackid"):
                tags.musicbrainz_track_id = text
            elif desc in ("musicbrainz release track id", "musicbrainz_releasetrackid"):
                tags.musicbrainz_release_track_id = text
            elif desc in ("musicbrainz artist id", "musicbrainz_artistid"):
                tags.musicbrainz_artist_id = text
            elif desc in ("musicbrainz album id", "musicbrainz_albumid"):
                tags.musicbrainz_album_id = text
            elif desc in ("musicbrainz album artist id", "musicbrainz_albumartistid"):
                tags.musicbrainz_album_artist_id = text
            elif desc in ("musicbrainz release group id", "musicbrainz_releasegroupid"):
                tags.musicbrainz_release_group_id = text
            elif "replaygain_track_gain" in desc:
                tags.replaygain_track_gain = _parse_float(text)
            elif "replaygain_track_peak" in desc:
                tags.replaygain_track_peak = _parse_float(text)
            elif "replaygain_album_gain" in desc:
                tags.replaygain_album_gain = _parse_float(text)
            elif "replaygain_album_peak" in desc:
                tags.replaygain_album_peak = _parse_float(text)

    # APIC cover art
    apic_frames: list[Any] = [f for f in id3.values() if isinstance(f, APIC)]
    if apic_frames:
        front: Any = next((f for f in apic_frames if getattr(f, "type", None) == 3), apic_frames[0])
        tags.embedded_cover = EmbeddedCover(
            data=bytes(getattr(front, "data", b"")),
            mime_type=str(getattr(front, "mime", "image/jpeg")),
            description=str(getattr(front, "desc", "")),
        )


def _extract_vorbis(tags_map: Any, audio: Any, tags: AudioTags) -> None:
    # Helper to get lowercased key
    lower_map: dict[str, Any] = {}
    for k, v in tags_map.items():
        lower_map[k.lower()] = v

    tags.title = _clean_str(lower_map.get("title"))
    tags.artist = _clean_str(lower_map.get("artist"))

    album_artist = (
        lower_map.get("albumartist")
        or lower_map.get("album_artist")
        or lower_map.get("ensemble")
        or lower_map.get("performer")
    )
    tags.album_artist = _clean_str(album_artist)
    tags.album = _clean_str(lower_map.get("album"))

    track_str = lower_map.get("tracknumber") or lower_map.get("track")
    tags.track_number, total_from_track = _parse_int_pair(track_str)
    total_str = lower_map.get("tracktotal") or lower_map.get("totaltracks")
    if total_str:
        tags.total_tracks, _ = _parse_int_pair(total_str)
    elif total_from_track:
        tags.total_tracks = total_from_track

    disc_str = lower_map.get("discnumber") or lower_map.get("disc")
    disc_num, total_from_disc = _parse_int_pair(disc_str)
    if disc_num:
        tags.disc_number = disc_num
    disctotal_str = lower_map.get("disctotal") or lower_map.get("totaldiscs")
    if disctotal_str:
        tags.total_discs, _ = _parse_int_pair(disctotal_str)
    elif total_from_disc:
        tags.total_discs = total_from_disc

    date_str = lower_map.get("date") or lower_map.get("year") or lower_map.get("originaldate")
    if date_str:
        tags.year, tags.release_date = _parse_year_and_date(date_str)

    genre_val = lower_map.get("genre")
    if genre_val:
        if isinstance(genre_val, (list, tuple)):
            tags.genres = [str(g).strip() for g in genre_val if str(g).strip()]
        else:
            tags.genres = [g.strip() for g in re.split(r"[/,;]", str(genre_val)) if g.strip()]

    comp_val = _clean_str(lower_map.get("compilation"))
    if comp_val in ("1", "true", "True"):
        tags.compilation = True

    # MusicBrainz
    tags.musicbrainz_track_id = _clean_str(lower_map.get("musicbrainz_trackid"))
    tags.musicbrainz_release_track_id = _clean_str(lower_map.get("musicbrainz_releasetrackid"))
    tags.musicbrainz_artist_id = _clean_str(lower_map.get("musicbrainz_artistid"))
    tags.musicbrainz_album_id = _clean_str(lower_map.get("musicbrainz_albumid"))
    tags.musicbrainz_album_artist_id = _clean_str(lower_map.get("musicbrainz_albumartistid"))
    tags.musicbrainz_release_group_id = _clean_str(lower_map.get("musicbrainz_releasegroupid"))

    # ReplayGain
    tags.replaygain_track_gain = _parse_float(lower_map.get("replaygain_track_gain"))
    tags.replaygain_track_peak = _parse_float(lower_map.get("replaygain_track_peak"))
    tags.replaygain_album_gain = _parse_float(lower_map.get("replaygain_album_gain"))
    tags.replaygain_album_peak = _parse_float(lower_map.get("replaygain_album_peak"))

    # Embedded cover from FLAC pictures attribute
    if hasattr(audio, "pictures") and audio.pictures:
        front = next((p for p in audio.pictures if p.type == 3), audio.pictures[0])
        tags.embedded_cover = EmbeddedCover(
            data=front.data,
            mime_type=front.mime or "image/jpeg",
            description=front.desc or "",
        )
    elif "metadata_block_picture" in lower_map:
        pic_blocks = lower_map["metadata_block_picture"]
        if not isinstance(pic_blocks, (list, tuple)):
            pic_blocks = [pic_blocks]
        for block in pic_blocks:
            try:
                raw_bytes = base64.b64decode(block)
                pic = Picture(raw_bytes)
                tags.embedded_cover = EmbeddedCover(
                    data=pic.data,
                    mime_type=pic.mime or "image/jpeg",
                    description=pic.desc or "",
                )
                break
            except Exception:
                continue


def _extract_mp4(mp4: MP4Tags, tags: AudioTags) -> None:
    tags.title = _clean_str(mp4.get("\xa9nam"))
    tags.artist = _clean_str(mp4.get("\xa9ART"))
    tags.album_artist = _clean_str(mp4.get("aART"))
    tags.album = _clean_str(mp4.get("\xa9alb"))

    if "trkn" in mp4:
        trkn_val = mp4["trkn"]
        if isinstance(trkn_val, list) and trkn_val:
            pair = trkn_val[0]
            if isinstance(pair, tuple) and len(pair) >= 2:
                tags.track_number = pair[0] if pair[0] > 0 else None
                tags.total_tracks = pair[1] if pair[1] > 0 else None

    if "disk" in mp4:
        disk_val = mp4["disk"]
        if isinstance(disk_val, list) and disk_val:
            pair = disk_val[0]
            if isinstance(pair, tuple) and len(pair) >= 2:
                tags.disc_number = pair[0] if pair[0] > 0 else 1
                tags.total_discs = pair[1] if pair[1] > 0 else None

    date_str = _clean_str(mp4.get("\xa9day"))
    if date_str:
        tags.year, tags.release_date = _parse_year_and_date(date_str)

    genre_str = _clean_str(mp4.get("\xa9gen"))
    if genre_str:
        tags.genres = [g.strip() for g in re.split(r"[/,;]", genre_str) if g.strip()]

    if mp4.get("cpil"):
        tags.compilation = True

    # Custom iTunes tags
    for key, val in mp4.items():
        if not key.startswith("----:com.apple.iTunes:"):
            continue
        name = key.removeprefix("----:com.apple.iTunes:").lower()
        val_str = _clean_str(val)
        if not val_str:
            continue
        if name in ("musicbrainz track id", "musicbrainz_trackid"):
            tags.musicbrainz_track_id = val_str
        elif name in ("musicbrainz release track id", "musicbrainz_releasetrackid"):
            tags.musicbrainz_release_track_id = val_str
        elif name in ("musicbrainz artist id", "musicbrainz_artistid"):
            tags.musicbrainz_artist_id = val_str
        elif name in ("musicbrainz album id", "musicbrainz_albumid"):
            tags.musicbrainz_album_id = val_str
        elif name in ("musicbrainz album artist id", "musicbrainz_albumartistid"):
            tags.musicbrainz_album_artist_id = val_str
        elif name in ("musicbrainz release group id", "musicbrainz_releasegroupid"):
            tags.musicbrainz_release_group_id = val_str
        elif "replaygain_track_gain" in name:
            tags.replaygain_track_gain = _parse_float(val_str)
        elif "replaygain_track_peak" in name:
            tags.replaygain_track_peak = _parse_float(val_str)
        elif "replaygain_album_gain" in name:
            tags.replaygain_album_gain = _parse_float(val_str)
        elif "replaygain_album_peak" in name:
            tags.replaygain_album_peak = _parse_float(val_str)

    # Embedded cover
    if "covr" in mp4 and mp4["covr"]:
        cover = mp4["covr"][0]
        mime = "image/jpeg"
        if getattr(cover, "imageformat", None) == MP4Cover.FORMAT_PNG:
            mime = "image/png"
        tags.embedded_cover = EmbeddedCover(data=bytes(cover), mime_type=mime)


def _extract_ape(ape: APEv2, tags: AudioTags) -> None:
    lower_map: dict[str, Any] = {k.lower(): v for k, v in ape.items()}

    tags.title = _clean_str(lower_map.get("title"))
    tags.artist = _clean_str(lower_map.get("artist"))
    tags.album_artist = _clean_str(
        lower_map.get("album artist") or lower_map.get("albumartist") or lower_map.get("ensemble")
    )
    tags.album = _clean_str(lower_map.get("album"))

    track_str = lower_map.get("track") or lower_map.get("tracknumber")
    tags.track_number, tags.total_tracks = _parse_int_pair(track_str)

    disc_str = lower_map.get("disc") or lower_map.get("discnumber")
    disc_num, tags.total_discs = _parse_int_pair(disc_str)
    if disc_num:
        tags.disc_number = disc_num

    date_str = lower_map.get("year") or lower_map.get("date")
    if date_str:
        tags.year, tags.release_date = _parse_year_and_date(date_str)

    genre_str = _clean_str(lower_map.get("genre"))
    if genre_str:
        tags.genres = [g.strip() for g in re.split(r"[/,;]", genre_str) if g.strip()]

    comp_str = _clean_str(lower_map.get("compilation"))
    if comp_str in ("1", "true", "True"):
        tags.compilation = True

    tags.musicbrainz_track_id = _clean_str(lower_map.get("musicbrainz_trackid"))
    tags.musicbrainz_release_track_id = _clean_str(lower_map.get("musicbrainz_releasetrackid"))
    tags.musicbrainz_artist_id = _clean_str(lower_map.get("musicbrainz_artistid"))
    tags.musicbrainz_album_id = _clean_str(lower_map.get("musicbrainz_albumid"))
    tags.musicbrainz_album_artist_id = _clean_str(lower_map.get("musicbrainz_albumartistid"))
    tags.musicbrainz_release_group_id = _clean_str(lower_map.get("musicbrainz_releasegroupid"))

    tags.replaygain_track_gain = _parse_float(lower_map.get("replaygain_track_gain"))
    tags.replaygain_track_peak = _parse_float(lower_map.get("replaygain_track_peak"))
    tags.replaygain_album_gain = _parse_float(lower_map.get("replaygain_album_gain"))
    tags.replaygain_album_peak = _parse_float(lower_map.get("replaygain_album_peak"))

    # Cover art
    cover_item = lower_map.get("cover art (front)")
    if cover_item:
        raw_val = cover_item.value if hasattr(cover_item, "value") else bytes(cover_item)
        if b"\x00" in raw_val:
            desc, data = raw_val.split(b"\x00", 1)
            mime = "image/png" if data.startswith(b"\x89PNG") else "image/jpeg"
            tags.embedded_cover = EmbeddedCover(
                data=data,
                mime_type=mime,
                description=desc.decode("utf-8", errors="ignore"),
            )


def read_audio_tags(
    path: str | Path,
    *,
    library_root: str | Path | None = None,
) -> AudioTags:
    """Read metadata tags from an audio file and resolve missing fields from path hints.

    Tags take priority over folder structure. When tags are missing or partial,
    folder hierarchy and filename patterns provide clean fallbacks.
    """
    file_path = Path(path)
    tags = AudioTags()
    ext = file_path.suffix.casefold()

    container_map = {
        ".mp3": "mp3",
        ".flac": "flac",
        ".m4a": "m4a",
        ".aac": "aac",
        ".ogg": "ogg",
        ".oga": "ogg",
        ".opus": "opus",
        ".wav": "wav",
        ".wv": "wavpack",
        ".ape": "ape",
        ".aif": "aiff",
        ".aiff": "aiff",
    }
    tags.container = container_map.get(ext, ext.lstrip("."))

    try:
        audio = mutagen.File(str(file_path))
    except Exception:
        audio = None

    if audio is not None:
        # Technical stream properties
        if hasattr(audio, "info") and audio.info is not None:
            info = audio.info
            tags.duration_seconds = (
                float(info.length) if hasattr(info, "length") and info.length else None
            )
            tags.bitrate = int(info.bitrate) if hasattr(info, "bitrate") and info.bitrate else None
            tags.sample_rate = (
                int(info.sample_rate) if hasattr(info, "sample_rate") and info.sample_rate else None
            )
            tags.channels = int(info.channels) if hasattr(info, "channels") and info.channels else 2

        # Format-specific tag extraction
        if audio.tags is not None:
            if isinstance(audio.tags, ID3):
                _extract_id3(audio.tags, tags)
            elif isinstance(audio.tags, MP4Tags):
                _extract_mp4(audio.tags, tags)
            elif isinstance(audio.tags, APEv2):
                _extract_ape(audio.tags, tags)
            elif isinstance(audio, FLAC) or hasattr(audio.tags, "items"):
                _extract_vorbis(audio.tags, audio, tags)

    # Apply folder and filename fallbacks
    apply_path_fallbacks(tags, file_path, library_root=library_root)
    return tags


def apply_path_fallbacks(
    tags: AudioTags,
    file_path: str | Path,
    *,
    library_root: str | Path | None = None,
) -> None:
    """Fill missing tags from filename and directory structure."""
    path = Path(file_path)
    filename = path.stem

    # 1. Filename pattern fallback for track number, track artist, and title
    match = _TRACK_FILE_PATTERN.match(filename)
    if match:
        groups = match.groupdict()
        if tags.track_number is None and groups.get("track"):
            with contextlib.suppress(ValueError):
                tags.track_number = int(groups["track"])
        if groups.get("disc") and tags.disc_number == 1:
            with contextlib.suppress(ValueError):
                tags.disc_number = int(groups["disc"])
        if not tags.artist and groups.get("artist"):
            tags.artist = groups["artist"].strip()
        if not tags.title and groups.get("title"):
            tags.title = groups["title"].strip()

    if not tags.title:
        tags.title = filename.strip()

    # 2. Directory structure for album, disc, and artist
    parent = path.parent
    norm_lib_root = Path(library_root).resolve() if library_root else None

    # Check if parent directory is a disc directory (e.g. CD 1, Disc 2)
    disc_match = _DISC_DIR_PATTERN.match(parent.name)
    if disc_match:
        if tags.disc_number == 1:
            with contextlib.suppress(ValueError):
                tags.disc_number = int(disc_match.group(1))
        album_dir = parent.parent
    else:
        album_dir = parent

    artist_dir = album_dir.parent

    # Album title fallback
    if not tags.album and album_dir:
        album_name = album_dir.name
        # If album folder contains year like "Discovery (2001)", extract year if missing
        year_match = re.search(r"\((19\d{2}|20\d{2})\)", album_name)
        if year_match and tags.year is None:
            tags.year = int(year_match.group(1))
            tags.album = album_name[: year_match.start()].strip()
        else:
            tags.album = album_name.strip()

    # Compilation and Album Artist fallback
    if not tags.album_artist:
        if tags.compilation:
            tags.album_artist = "Various Artists"
        elif artist_dir and (norm_lib_root is None or artist_dir.resolve() != norm_lib_root):
            if artist_dir.name.casefold() in (
                "various artists",
                "various",
                "compilations",
                "soundtracks",
                "soundtrack",
                "ost",
            ):
                tags.compilation = True
                tags.album_artist = "Various Artists"
            else:
                tags.album_artist = artist_dir.name.strip()
        elif tags.artist:
            tags.album_artist = tags.artist
        else:
            tags.album_artist = "Unknown Artist"

    # Track artist fallback
    if not tags.artist:
        tags.artist = tags.album_artist or "Unknown Artist"
