"""Map Hearth ORM models into Jellyfin-compatible JSON DTOs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.db.models import (
    Image as ImageModel,
)
from app.db.models import (
    Library,
    MediaFile,
    MediaItem,
    User,
    UserItemState,
)
from app.services.media.probe import ProbeResult
from app.services.playback_state import chapters_from_streams
from app.services.subtitles import discover_subtitles, encode_subtitle_id, subtitle_language

TICKS_PER_SECOND = 10_000_000
TICKS_PER_MS = 10_000

_LIBRARY_COLLECTION_TYPE = {
    "movie": "movies",
    "tv": "tvshows",
    "music": "music",
    "book": "books",
}

_ITEM_TYPE_MAP = {
    "movie": "Movie",
    "series": "Series",
    "season": "Season",
    "episode": "Episode",
    "artist": "MusicArtist",
    "album": "MusicAlbum",
    "track": "Audio",
    "book": "Book",
}

_MEDIA_TYPE_MAP = {
    "movie": "Video",
    "episode": "Video",
    "track": "Audio",
    "book": "Book",
}

_IMAGE_KIND_MAP = {
    "poster": "Primary",
    "backdrop": "Backdrop",
    "logo": "Logo",
    "thumb": "Thumb",
    "banner": "Banner",
}


def seconds_to_ticks(seconds: float | None) -> int | None:
    """Convert seconds to Jellyfin 100-nanosecond ticks."""
    if seconds is None or seconds < 0:
        return None
    return int(round(seconds * TICKS_PER_SECOND))


def ms_to_ticks(ms: int | None) -> int:
    """Convert milliseconds to Jellyfin 100-nanosecond ticks."""
    if ms is None or ms <= 0:
        return 0
    return int(ms) * TICKS_PER_MS


def ticks_to_ms(ticks: int | None) -> int:
    """Convert Jellyfin ticks to milliseconds."""
    if ticks is None or ticks <= 0:
        return 0
    return int(ticks // TICKS_PER_MS)


def format_jf_datetime(dt: datetime | None) -> str | None:
    """Format a datetime as ISO-8601 UTC string ending with Z."""
    if dt is None:
        return None
    aware = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    return aware.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


def user_to_dto(user: User, server_id: str) -> dict[str, Any]:
    """Convert a Hearth User into a Jellyfin UserDto."""
    is_admin = user.role == "admin"
    return {
        "Name": user.username,
        "ServerId": server_id,
        "Id": user.id,
        "HasPassword": True,
        "HasConfiguredPassword": True,
        "HasConfiguredEasyPassword": False,
        "EnableAutoLogin": False,
        "LastLoginDate": format_jf_datetime(user.last_login_at),
        "LastActivityDate": format_jf_datetime(user.last_login_at or user.updated_at),
        "Configuration": {
            "AudioLanguagePreference": user.preferred_audio_language or "",
            "PlayDefaultAudioTrack": True,
            "SubtitleLanguagePreference": user.preferred_subtitle_language or "",
            "DisplayMissingEpisodes": False,
            "GroupedFolders": [],
            "SubtitleMode": "Always" if user.subtitle_mode == "always" else "Default",
            "DisplayCollectionsView": False,
            "EnableLocalPassword": False,
            "OrderedViews": [],
            "LatestItemsExcludes": [],
            "MyMediaExcludes": [],
            "HidePlayedInLatest": True,
            "RememberAudioSelections": True,
            "RememberSubtitleSelections": True,
            "EnableNextEpisodeAutoPlay": user.auto_play_next != "no",
        },
        "Policy": {
            "IsAdministrator": is_admin,
            "IsHidden": False,
            "IsDisabled": user.disabled,
            "BlockedTags": [],
            "AllowedTags": [],
            "EnableUserPreferenceAccess": True,
            "AccessSchedules": [],
            "BlockUnratedItems": [],
            "EnableRemoteControlOfOtherUsers": is_admin,
            "EnableSharedDeviceControl": is_admin,
            "EnableRemoteAccess": True,
            "EnableLiveTvManagement": False,
            "EnableLiveTvAccess": False,
            "EnableMediaPlayback": True,
            "EnableAudioPlaybackTranscoding": user.allow_transcode,
            "EnableVideoPlaybackTranscoding": user.allow_transcode,
            "EnablePlaybackRemuxing": True,
            "ForceRemoteSourceTranscoding": False,
            "EnableContentDeletion": False,
            "EnableContentDeletionFromFolders": [],
            "EnableContentDownloading": True,
            "EnableSyncTranscoding": True,
            "EnableMediaConversion": False,
            "EnabledDevices": [],
            "EnableAllDevices": True,
            "EnabledChannels": [],
            "EnableAllChannels": True,
            "EnabledFolders": [],
            "EnableAllFolders": True,
            "InvalidLoginAttemptCount": 0,
            "LoginAttemptsBeforeLockout": -1,
            "MaxActiveSessions": 0,
            "EnablePublicSharing": True,
            "RemoteClientBitrateLimit": 0,
            "AuthenticationProviderId": "Hearth.LocalAuth",
            "PasswordResetProviderId": "Hearth.LocalAuth",
            "SyncPlayAccess": "CreateAndJoinGroups",
        },
    }


def library_to_view_dto(library: Library, server_id: str) -> dict[str, Any]:
    """Convert a Hearth Library into a Jellyfin CollectionFolder view DTO."""
    return {
        "Name": library.name,
        "ServerId": server_id,
        "Id": library.id,
        "Etag": library.id,
        "DateCreated": format_jf_datetime(library.created_at),
        "CanDelete": False,
        "CanDownload": False,
        "SortName": library.name.lower(),
        "Type": "CollectionFolder",
        "CollectionType": _LIBRARY_COLLECTION_TYPE.get(library.type, "movies"),
        "IsFolder": True,
        "ImageTags": {},
        "BackdropImageTags": [],
        "UserData": {
            "PlaybackPositionTicks": 0,
            "PlayCount": 0,
            "IsFavorite": False,
            "Played": False,
            "Key": library.id,
        },
    }


def _image_tags_for_item(images: list[ImageModel]) -> tuple[dict[str, str], list[str]]:
    image_tags: dict[str, str] = {}
    backdrop_tags: list[str] = []
    ordered = sorted(images, key=lambda img: (not img.is_primary, img.created_at))
    for img in ordered:
        jf_type = _IMAGE_KIND_MAP.get(img.kind)
        if jf_type is None:
            continue
        tag = img.id.replace("-", "")[:16]
        if jf_type == "Backdrop":
            backdrop_tags.append(tag)
        elif jf_type not in image_tags:
            image_tags[jf_type] = tag
    return image_tags, backdrop_tags


def _item_files(item: MediaItem) -> list[MediaFile]:
    if item.type == "episode" and item.episode and item.episode.file is not None:
        files = [item.episode.file]
    elif item.type == "track" and item.track and item.track.file is not None:
        files = [item.track.file]
    else:
        files = list(item.files)
    return [f for f in files if f.missing_since is None]


def _probe_from_file(media_file: MediaFile) -> ProbeResult:
    fallback_container = media_file.container or "mp4"
    try:
        data = {"container": fallback_container, **(media_file.streams_json or {})}
        return ProbeResult.model_validate(data)
    except Exception:
        return ProbeResult(container=fallback_container)


def build_media_sources(
    item: MediaItem,
    *,
    token: str | None = None,
    allow_transcoding: bool = True,
) -> list[dict[str, Any]]:
    """Build Jellyfin MediaSourceInfo entries from an item's registered MediaFiles."""
    sources: list[dict[str, Any]] = []
    files = _item_files(item)
    is_audio = item.type == "track"
    is_video = item.type in {"movie", "episode"}
    token_qs = f"&api_key={quote(token)}" if token else ""

    for media_file in files:
        probe = _probe_from_file(media_file)
        container = (
            media_file.container or Path(media_file.path).suffix.lstrip(".") or "mp4"
        ).lower()
        duration_s = media_file.duration_seconds or item.runtime_seconds
        run_time_ticks = seconds_to_ticks(duration_s)

        streams: list[dict[str, Any]] = []
        for idx, stream in enumerate(probe.streams):
            stream_index = stream.index if stream.index is not None else idx
            if stream.codec_type == "video":
                streams.append(
                    {
                        "Codec": stream.codec_name or "h264",
                        "Language": stream.language,
                        "Title": stream.title,
                        "Index": stream_index,
                        "IsDefault": stream.is_default,
                        "IsForced": stream.is_forced,
                        "Type": "Video",
                        "Width": stream.width,
                        "Height": stream.height,
                        "BitRate": stream.bit_rate,
                        "VideoRange": "HDR" if stream.is_hdr else "SDR",
                        "IsExternal": False,
                    }
                )
            elif stream.codec_type == "audio":
                streams.append(
                    {
                        "Codec": stream.codec_name or "aac",
                        "Language": stream.language,
                        "Title": stream.title,
                        "Index": stream_index,
                        "IsDefault": stream.is_default,
                        "IsForced": stream.is_forced,
                        "Type": "Audio",
                        "Channels": stream.channels or 2,
                        "SampleRate": stream.sample_rate,
                        "BitRate": stream.bit_rate,
                        "IsExternal": False,
                    }
                )
            elif stream.codec_type == "subtitle":
                streams.append(
                    {
                        "Codec": stream.codec_name or "subrip",
                        "Language": stream.language,
                        "Title": stream.title,
                        "Index": stream_index,
                        "IsDefault": stream.is_default,
                        "IsForced": stream.is_forced,
                        "Type": "Subtitle",
                        "IsExternal": False,
                        "DeliveryMethod": "External"
                        if (stream.codec_name or "").lower()
                        in {"subrip", "srt", "ass", "ssa", "webvtt", "vtt"}
                        else "Embed",
                        "DeliveryUrl": (
                            f"/Videos/{item.id}/{media_file.id}/Subtitles/{stream_index}/0/Stream.vtt"
                            f"?{token_qs.lstrip('&')}"
                        ).rstrip("?"),
                    }
                )

        # Include sidecar subtitles as external subtitle streams
        if is_video and media_file.sidecar_subtitles:
            next_idx = max((s["Index"] for s in streams), default=-1) + 1
            for sub_path in discover_subtitles(media_file.path):
                sub_id = encode_subtitle_id(media_file.id, sub_path.name)
                lang = subtitle_language(media_file.path, sub_path)
                streams.append(
                    {
                        "Codec": sub_path.suffix.lstrip(".").lower() or "srt",
                        "Language": lang,
                        "Title": sub_path.stem,
                        "Index": next_idx,
                        "IsDefault": False,
                        "IsForced": False,
                        "Type": "Subtitle",
                        "IsExternal": True,
                        "DeliveryMethod": "External",
                        "DeliveryUrl": (
                            f"/Videos/{item.id}/{media_file.id}/Subtitles/{next_idx}/0/Stream.vtt"
                            f"?subId={quote(sub_id)}{token_qs}"
                        ),
                    }
                )
                next_idx += 1

        if is_audio:
            direct_url = (
                f"/Audio/{item.id}/stream?Static=true&MediaSourceId={media_file.id}{token_qs}"
            )
            transcode_url = None
        else:
            direct_url = (
                f"/Videos/{item.id}/stream?Static=true&MediaSourceId={media_file.id}{token_qs}"
            )
            transcode_url = (
                f"/Videos/{item.id}/master.m3u8?MediaSourceId={media_file.id}{token_qs}"
                if allow_transcoding
                else None
            )

        sources.append(
            {
                "Protocol": "File",
                "Id": media_file.id,
                "Path": media_file.path,
                "Type": "Default",
                "Container": container,
                "Size": media_file.size_bytes,
                "Name": media_file.version_label or Path(media_file.path).stem,
                "IsRemote": False,
                "RunTimeTicks": run_time_ticks,
                "SupportsTranscoding": bool(allow_transcoding and is_video),
                "SupportsDirectStream": True,
                "SupportsDirectPlay": True,
                "IsInfiniteStream": False,
                "RequiresOpening": False,
                "RequiresClosing": False,
                "RequiresLooping": False,
                "SupportsProbing": True,
                "MediaStreams": streams,
                "Bitrate": media_file.bit_rate,
                "DefaultAudioStreamIndex": next(
                    (s["Index"] for s in streams if s["Type"] == "Audio" and s["IsDefault"]),
                    next((s["Index"] for s in streams if s["Type"] == "Audio"), None),
                ),
                "DirectStreamUrl": direct_url,
                "TranscodingUrl": transcode_url,
                "TranscodingSubProtocol": "hls" if transcode_url else None,
                "TranscodingContainer": "mp4" if transcode_url else None,
            }
        )
    return sources


def user_data_dto(
    item_id: str,
    state: UserItemState | None,
    *,
    unplayed_count: int | None = None,
) -> dict[str, Any]:
    """Build a Jellyfin UserItemDataDto."""
    data: dict[str, Any] = {
        "PlaybackPositionTicks": ms_to_ticks(state.position_ms if state else 0),
        "PlayCount": state.play_count if state else 0,
        "IsFavorite": bool(state.favorite) if state else False,
        "Played": bool(state.played) if state else False,
        "Key": item_id,
    }
    if state and state.last_played_at:
        data["LastPlayedDate"] = format_jf_datetime(state.last_played_at)
    if unplayed_count is not None:
        data["UnplayedItemCount"] = unplayed_count
    return data


def media_item_to_dto(
    item: MediaItem,
    server_id: str,
    *,
    user_state: UserItemState | None = None,
    genres: list[str] | None = None,
    people: list[dict[str, Any]] | None = None,
    include_media_sources: bool = False,
    token: str | None = None,
    allow_transcoding: bool = True,
    unplayed_count: int | None = None,
    child_count: int | None = None,
) -> dict[str, Any]:
    """Convert a Hearth MediaItem into a Jellyfin BaseItemDto."""
    jf_type = _ITEM_TYPE_MAP.get(item.type, "Video")
    is_folder = item.type in {"series", "season", "artist", "album"}
    image_tags, backdrop_tags = _image_tags_for_item(list(item.images))

    files = _item_files(item)
    primary_file = files[0] if files else None
    runtime_seconds = item.runtime_seconds or (
        primary_file.duration_seconds if primary_file else None
    )
    if item.type == "track" and item.track and item.track.duration_seconds:
        runtime_seconds = item.track.duration_seconds

    dto: dict[str, Any] = {
        "Name": item.title,
        "ServerId": server_id,
        "Id": item.id,
        "Etag": item.id,
        "DateCreated": format_jf_datetime(item.added_at),
        "SortName": item.sort_title,
        "Type": jf_type,
        "IsFolder": is_folder,
        "Overview": item.overview,
        "ProductionYear": item.year,
        "CommunityRating": item.rating,
        "OfficialRating": item.content_rating,
        "RunTimeTicks": seconds_to_ticks(runtime_seconds),
        "ParentId": item.library_id,
        "ImageTags": image_tags,
        "BackdropImageTags": backdrop_tags,
        "ProviderIds": {str(k).title(): str(v) for k, v in (item.provider_ids or {}).items()},
        "UserData": user_data_dto(item.id, user_state, unplayed_count=unplayed_count),
        "CanDelete": False,
        "CanDownload": bool(files),
        "LocationType": "FileSystem",
    }

    if item.type in _MEDIA_TYPE_MAP:
        dto["MediaType"] = _MEDIA_TYPE_MAP[item.type]

    if genres is not None:
        dto["Genres"] = genres
    elif item.genres:
        dto["Genres"] = [g.name for g in item.genres]
    else:
        dto["Genres"] = []

    if people is not None:
        dto["People"] = people

    if child_count is not None:
        dto["ChildCount"] = child_count

    # Type-specific fields
    if item.type == "movie" and item.movie:
        dto["OriginalTitle"] = item.movie.original_title or item.title
        if item.movie.tagline:
            dto["Taglines"] = [item.movie.tagline]
        if item.movie.release_date:
            dto["PremiereDate"] = f"{item.movie.release_date}T00:00:00.0000000Z"

    elif item.type == "series" and item.series:
        dto["OriginalTitle"] = item.series.original_title or item.title
        dto["Status"] = item.series.status
        if item.series.first_air_date:
            dto["PremiereDate"] = f"{item.series.first_air_date}T00:00:00.0000000Z"
        if item.series.network:
            dto["Studios"] = [{"Name": item.series.network}]

    elif item.type == "season" and item.season:
        dto["IndexNumber"] = item.season.season_number
        dto["SeriesId"] = item.season.series_id
        dto["ParentId"] = item.season.series_id
        if item.season.series and item.season.series.item:
            dto["SeriesName"] = item.season.series.item.title
        if item.season.air_date:
            dto["PremiereDate"] = f"{item.season.air_date}T00:00:00.0000000Z"

    elif item.type == "episode" and item.episode:
        dto["IndexNumber"] = item.episode.episode_number
        dto["ParentIndexNumber"] = item.episode.season_number
        dto["SeriesId"] = item.episode.series_id
        dto["SeasonId"] = item.episode.season_id
        dto["ParentId"] = item.episode.season_id or item.episode.series_id
        if item.episode.series and item.episode.series.item:
            dto["SeriesName"] = item.episode.series.item.title
        if item.episode.season and item.episode.season.item:
            dto["SeasonName"] = item.episode.season.item.title
        if item.episode.air_date:
            dto["PremiereDate"] = f"{item.episode.air_date}T00:00:00.0000000Z"

    elif item.type == "album" and item.album:
        dto["ParentId"] = item.album.artist_id or item.library_id
        if item.album.album_artist:
            dto["AlbumArtist"] = item.album.album_artist
            dto["AlbumArtists"] = [
                {"Name": item.album.album_artist, "Id": item.album.artist_id or item.id}
            ]
        elif item.album.artist and item.album.artist.item:
            dto["AlbumArtist"] = item.album.artist.item.title
            dto["AlbumArtists"] = [
                {"Name": item.album.artist.item.title, "Id": item.album.artist.item_id}
            ]
        if item.album.release_date:
            dto["PremiereDate"] = f"{item.album.release_date}T00:00:00.0000000Z"

    elif item.type == "track" and item.track:
        dto["IndexNumber"] = item.track.track_number
        dto["ParentIndexNumber"] = item.track.disc_number
        dto["AlbumId"] = item.track.album_id
        dto["ParentId"] = item.track.album_id or item.library_id
        if item.track.album and item.track.album.item:
            dto["Album"] = item.track.album.item.title
        if item.track.artist and item.track.artist.item:
            dto["Artists"] = [item.track.artist.item.title]
            dto["ArtistItems"] = [
                {"Name": item.track.artist.item.title, "Id": item.track.artist.item_id}
            ]
            dto["AlbumArtist"] = item.track.artist.item.title

    if primary_file is not None:
        dto["Container"] = primary_file.container or Path(primary_file.path).suffix.lstrip(".")
        dto["Path"] = primary_file.path
        chapters = chapters_from_streams(primary_file.streams_json)
        if chapters:
            dto["Chapters"] = [
                {
                    "StartPositionTicks": seconds_to_ticks(ch.get("start_seconds")) or 0,
                    "Name": ch.get("title") or f"Chapter {i + 1}",
                }
                for i, ch in enumerate(chapters)
            ]

    if include_media_sources and files:
        dto["MediaSources"] = build_media_sources(
            item, token=token, allow_transcoding=allow_transcoding
        )

    return dto
