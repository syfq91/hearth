"""Client capability profiles and preset configurations.

Translates browser/device profiles into actionable playback constraints.
Clients may select a built-in preset ('chrome', 'firefox', 'safari',
'android_tv', 'unknown') and optionally override specific capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PresetName = Literal["chrome", "firefox", "safari", "android_tv", "unknown"]
QualityLevel = Literal["auto", "sd", "hd", "fhd", "4k"]


class SubtitleProfile(BaseModel):
    """Subtitle delivery capability for a specific subtitle format."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    format: str = ""
    method: str = ""


class TranscodeTarget(BaseModel):
    """One ordered, conditional transcode target.

    Conditions are a small typed subset (container plus ``max_*`` ceilings),
    deliberately not a general expression tree. A target is applicable when its
    container is the HLS fMP4 packager and at least one of its codecs can be
    produced by this server; its ceilings shape the chosen encoder's output.
    """

    model_config = ConfigDict(extra="ignore")

    container: str | None = Field(default=None, max_length=32)
    video_codecs: list[str] = Field(default_factory=list)
    audio_codecs: list[str] = Field(default_factory=list)
    max_width: int | None = Field(default=None, gt=0)
    max_height: int | None = Field(default=None, gt=0)
    max_bitrate: int | None = Field(default=None, gt=0)
    max_video_level: int | None = Field(default=None, gt=0)
    max_audio_channels: int | None = Field(default=None, gt=0)


class ClientCapabilities(BaseModel):
    """What a client device or browser player supports."""

    model_config = ConfigDict(extra="ignore")

    preset: str | None = Field(default=None, max_length=64)
    containers: list[str] = Field(default_factory=list)
    video_codecs: list[str] = Field(default_factory=list)
    video_profiles: list[str] = Field(default_factory=list)
    audio_codecs: list[str] = Field(default_factory=list)
    # "Produce this" rules, ordered by preference. Unlike the pass-through lists
    # above, these name encoder targets, not formats accepted as-is. The server
    # filters them by what it can encode and by the HLS codec allow-list.
    video_transcode_codecs: list[str] = Field(default_factory=lambda: ["h264"])
    audio_transcode_codecs: list[str] = Field(default_factory=lambda: ["aac"])
    # Richer, ordered + conditional targets. Empty means use the flat lists above.
    transcode_targets: list[TranscodeTarget] = Field(default_factory=list)
    max_width: int | None = Field(default=None, gt=0)
    max_height: int | None = Field(default=None, gt=0)
    max_bitrate: int | None = Field(default=None, gt=0)
    max_video_level: int | None = Field(default=None, gt=0)
    max_audio_channels: int | None = Field(default=None, gt=0)
    max_audio_sample_rate: int | None = Field(default=None, gt=0)
    subtitle_formats: list[str] = Field(default_factory=list)
    subtitle_profiles: list[SubtitleProfile] = Field(default_factory=list)
    hdr: bool = False

    def supports_container(self, container: str, *, extension: str | None = None) -> bool:
        """Check if container extension/name is supported.

        ffprobe reports ``format_name`` as the comma-joined list of demuxer
        aliases (``mov,mp4,m4a,3gp,3g2,mj2`` for a plain mp4), so the stored
        container can name several formats; any supported alias matches.

        ``extension`` disambiguates aliases that share one demuxer but are
        distinct client-facing containers. Matroska and WebM both report
        ``matroska,webm``, so without the real file extension a WebM-capable
        client would be considered able to direct-play MKV.
        """
        supported = {c.lower().lstrip(".") for c in self.containers}
        aliases = [
            alias.strip() for alias in container.lower().lstrip(".").split(",") if alias.strip()
        ]
        suffix = extension.lower().lstrip(".") if extension else None
        if suffix == "mkv" and "matroska" in aliases:
            aliases = ["matroska", "mkv"]
        elif suffix == "webm" and "webm" in aliases:
            aliases = ["webm"]

        for target in aliases:
            # Handle mp4/mov/m4v aliases
            normalized = "mp4" if target in ("mov", "m4v") else target
            if target in supported or normalized in supported:
                return True
        return False

    def supports_video_codec(self, codec: str, profile: str | None = None) -> bool:
        """Check if video codec and optional profile are supported."""
        c_lower = codec.lower()
        aliases = {c_lower}
        is_h264 = c_lower in ("h264", "avc", "avc1")
        is_hevc = c_lower in ("hevc", "h265", "hev1", "hvc1")

        if is_h264:
            aliases.update(["h264", "avc", "avc1"])
        elif is_hevc:
            aliases.update(["hevc", "h265", "hev1", "hvc1"])
        elif c_lower == "vp9":
            aliases.update(["vp9"])
        elif c_lower in ("av1", "av01"):
            aliases.update(["av1", "av01"])

        supported = any(c.lower() in aliases for c in self.video_codecs)
        if not supported:
            return False

        if profile and self.video_profiles:
            p_lower = profile.lower()
            if is_h264:
                # 10-bit H.264 profiles (e.g. High 10) must not match 8-bit 'high'
                if "10" in p_lower and not any("10" in p.lower() for p in self.video_profiles):
                    return False
                if any(h in p_lower for h in ("baseline", "main", "high")):
                    for p in self.video_profiles:
                        target = p.lower()
                        if target == p_lower:
                            return True
                        if (
                            target in ("baseline", "main", "high")
                            and "10" not in p_lower
                            and target in p_lower
                        ):
                            return True
                        if "10" in target and "10" in p_lower:
                            return True
                    return False
            if is_hevc and "main 10" in p_lower:
                return any("main 10" in p.lower() for p in self.video_profiles)
        return True

    def supports_audio_codec(self, codec: str) -> bool:
        """Check if audio codec is supported."""
        c_lower = codec.lower()
        aliases = {c_lower}
        if c_lower in ("aac", "mp4a"):
            aliases.update(["aac", "mp4a"])
        elif c_lower in ("mp3", "mp3float"):
            aliases.update(["mp3", "mp3float"])
        elif c_lower in ("ac3", "ac-3"):
            aliases.update(["ac3", "ac-3"])
        elif c_lower in ("eac3", "e-ac-3", "ec-3"):
            aliases.update(["eac3", "e-ac-3", "ec-3"])
        return any(a.lower() in aliases for a in self.audio_codecs)

    def supports_subtitle(self, format_name: str) -> bool:
        """Check if subtitle format is supported natively."""
        f_lower = format_name.lower().lstrip(".")
        return any(s.lower().lstrip(".") == f_lower for s in self.subtitle_formats)


BUILTIN_PRESETS: dict[str, ClientCapabilities] = {
    "chrome": ClientCapabilities(
        preset="chrome",
        containers=["mp4", "webm", "m4a", "mp3", "ogg", "flac"],
        video_codecs=["h264", "vp9", "av1"],
        video_profiles=["baseline", "main", "high"],
        audio_codecs=["aac", "opus", "mp3", "flac"],
        max_width=3840,
        max_height=2160,
        max_bitrate=None,
        subtitle_formats=["vtt"],
        hdr=False,
    ),
    "firefox": ClientCapabilities(
        preset="firefox",
        containers=["mp4", "webm", "ogg", "flac", "mp3"],
        video_codecs=["h264", "vp9", "av1"],
        video_profiles=["baseline", "main", "high"],
        audio_codecs=["aac", "opus", "mp3", "flac"],
        max_width=3840,
        max_height=2160,
        max_bitrate=None,
        subtitle_formats=["vtt"],
        hdr=False,
    ),
    "safari": ClientCapabilities(
        preset="safari",
        containers=["mp4", "m4a", "mp3"],
        video_codecs=["h264", "hevc"],
        video_profiles=["baseline", "main", "high", "main 10"],
        audio_codecs=["aac", "alac", "mp3", "ac3", "eac3"],
        max_width=3840,
        max_height=2160,
        max_bitrate=None,
        subtitle_formats=["vtt"],
        hdr=True,
    ),
    "android_tv": ClientCapabilities(
        preset="android_tv",
        containers=["mp4", "mkv", "webm", "ts"],
        video_codecs=["h264", "hevc", "vp9"],
        video_profiles=["baseline", "main", "high", "main 10"],
        audio_codecs=["aac", "ac3", "eac3", "dts", "opus", "mp3"],
        max_width=3840,
        max_height=2160,
        max_bitrate=None,
        subtitle_formats=["vtt", "srt"],
        hdr=True,
    ),
    "unknown": ClientCapabilities(
        preset="unknown",
        containers=["mp4"],
        video_codecs=["h264"],
        video_profiles=["baseline", "main", "high"],
        audio_codecs=["aac", "mp3"],
        max_width=1920,
        max_height=1080,
        max_bitrate=4_000_000,
        subtitle_formats=["vtt"],
        hdr=False,
    ),
}


@dataclass(frozen=True)
class QualityLimit:
    """Ceilings imposed by a user-facing quality label.

    A missing ceiling means the label does not constrain that dimension:
    ``auto`` leaves device and user limits untouched.
    """

    max_height: int | None = None
    max_bitrate: int | None = None


QUALITY_LIMITS: dict[str, QualityLimit] = {
    "auto": QualityLimit(),
    "sd": QualityLimit(max_height=480, max_bitrate=1_500_000),
    "hd": QualityLimit(max_height=720, max_bitrate=3_000_000),
    "fhd": QualityLimit(max_height=1080, max_bitrate=6_000_000),
    "4k": QualityLimit(max_height=2160, max_bitrate=20_000_000),
}


def apply_quality_ceiling(capabilities: ClientCapabilities, quality: QualityLevel) -> None:
    """Narrow ``capabilities`` in place with a quality label's ceilings.

    These are ceilings, not targets: a source already below them still direct
    plays. Device and per-user limits continue to win because each value is
    intersected with what the client already declared.
    """
    limit = QUALITY_LIMITS.get(quality, QUALITY_LIMITS["auto"])
    if limit.max_height is not None:
        current = capabilities.max_height
        capabilities.max_height = (
            min(current, limit.max_height) if current is not None else limit.max_height
        )
    if limit.max_bitrate is not None:
        current = capabilities.max_bitrate
        capabilities.max_bitrate = (
            min(current, limit.max_bitrate) if current is not None else limit.max_bitrate
        )


def detect_preset_from_user_agent(user_agent: str | None) -> PresetName:
    """Infer the built-in preset from a User-Agent header string.

    Recognizes Chrome (and Chromium-based browsers), Firefox, Safari, and
    Android TV. Unrecognized or missing strings fall back to 'unknown'.
    """
    if not user_agent:
        return "unknown"
    ua = user_agent.lower()

    # Android TV / Fire TV check
    if "android" in ua and any(k in ua for k in ("tv", "aft", "bravia", "smarttv")):
        return "android_tv"

    # Firefox check (desktop & mobile)
    if "firefox" in ua or "fxios" in ua:
        return "firefox"

    # Chrome / Chromium check (including Edge, Opera, Samsung, CriOS)
    # Must precede Safari check because Chrome UAs include "Safari/537.36"
    if "chrome" in ua or "crios" in ua or "chromium" in ua:
        return "chrome"

    # Safari check (desktop & iOS Mobile Safari)
    if "safari" in ua and any(k in ua for k in ("version", "mobile", "applewebkit")):
        return "safari"

    return "unknown"


def resolve_client_capabilities(
    client: ClientCapabilities | None = None,
    user_agent: str | None = None,
) -> ClientCapabilities:
    """Resolve preset and apply any client-provided overrides.

    Explicit capabilities win over User-Agent presets. When capabilities are
    absent, User-Agent detection chooses among 'chrome', 'firefox', 'safari',
    'android_tv', or falls back to 'unknown'.
    """
    preset_name: str | None = None
    if client is not None and client.preset:
        preset_name = client.preset.lower().strip()
    elif user_agent:
        preset_name = detect_preset_from_user_agent(user_agent)

    if not preset_name or preset_name not in BUILTIN_PRESETS:
        preset_name = "unknown"

    base = BUILTIN_PRESETS[preset_name].model_copy(deep=True)

    if client is None:
        return base

    # If preset was not explicitly set on client, but client provided explicit codecs/containers,
    # start fresh with empty capabilities so explicit capabilities win over preset defaults
    if not client.preset and (client.containers or client.video_codecs or client.audio_codecs):
        base = ClientCapabilities()

    # Override non-empty lists and explicitly set values
    if client.containers:
        base.containers = [c.lower().strip() for c in client.containers]
    if client.video_codecs:
        base.video_codecs = [c.lower().strip() for c in client.video_codecs]
    if client.video_profiles:
        base.video_profiles = [p.lower().strip() for p in client.video_profiles]
    if client.audio_codecs:
        base.audio_codecs = [a.lower().strip() for a in client.audio_codecs]
    if client.video_transcode_codecs:
        base.video_transcode_codecs = [c.lower().strip() for c in client.video_transcode_codecs]
    if client.audio_transcode_codecs:
        base.audio_transcode_codecs = [c.lower().strip() for c in client.audio_transcode_codecs]
    if client.transcode_targets:
        targets: list[TranscodeTarget] = []
        for target in client.transcode_targets:
            normalized = target.model_copy(deep=True)
            if normalized.container:
                normalized.container = normalized.container.lower().strip()
            normalized.video_codecs = [c.lower().strip() for c in target.video_codecs]
            normalized.audio_codecs = [c.lower().strip() for c in target.audio_codecs]
            targets.append(normalized)
        base.transcode_targets = targets
    if client.subtitle_formats:
        base.subtitle_formats = [s.lower().strip() for s in client.subtitle_formats]
    if client.subtitle_profiles:
        base.subtitle_profiles = [p.model_copy() for p in client.subtitle_profiles]
    if client.max_width is not None:
        base.max_width = client.max_width
    if client.max_height is not None:
        base.max_height = client.max_height
    if client.max_bitrate is not None:
        base.max_bitrate = client.max_bitrate
    if client.max_video_level is not None:
        base.max_video_level = client.max_video_level
    if client.max_audio_channels is not None:
        base.max_audio_channels = client.max_audio_channels
    if client.max_audio_sample_rate is not None:
        base.max_audio_sample_rate = client.max_audio_sample_rate
    if client.hdr:
        base.hdr = True
    if client.preset:
        base.preset = client.preset

    return base


__all__ = [
    "BUILTIN_PRESETS",
    "ClientCapabilities",
    "PresetName",
    "QUALITY_LIMITS",
    "QualityLevel",
    "QualityLimit",
    "SubtitleProfile",
    "TranscodeTarget",
    "apply_quality_ceiling",
    "detect_preset_from_user_agent",
    "resolve_client_capabilities",
]
