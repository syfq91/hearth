"""Client capability profiles and preset configurations.

Translates browser/device profiles into actionable playback constraints.
Clients may select a built-in preset ('chrome', 'firefox', 'safari',
'android_tv', 'unknown') and optionally override specific capabilities.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PresetName = Literal["chrome", "firefox", "safari", "android_tv", "unknown"]


class ClientCapabilities(BaseModel):
    """What a client device or browser player supports."""

    preset: str | None = Field(default=None, max_length=64)
    containers: list[str] = Field(default_factory=list)
    video_codecs: list[str] = Field(default_factory=list)
    video_profiles: list[str] = Field(default_factory=list)
    audio_codecs: list[str] = Field(default_factory=list)
    max_width: int | None = Field(default=None, gt=0)
    max_height: int | None = Field(default=None, gt=0)
    max_bitrate: int | None = Field(default=None, gt=0)
    subtitle_formats: list[str] = Field(default_factory=list)
    hdr: bool = False
    hls_fmp4: bool = False

    def supports_container(self, container: str) -> bool:
        """Check if container extension/name is supported."""
        target = container.lower().lstrip(".")
        # Handle mp4/mov/m4v aliases
        normalized_target = "mp4" if target in ("mov", "m4v") else target
        return any(c.lower().lstrip(".") in (target, normalized_target) for c in self.containers)

    def supports_video_codec(self, codec: str, profile: str | None = None) -> bool:
        """Check if video codec and optional profile are supported."""
        c_lower = codec.lower()
        # Aliases
        aliases = {c_lower}
        if c_lower in ("h264", "avc", "avc1"):
            aliases.update(["h264", "avc", "avc1"])
        elif c_lower in ("hevc", "h265", "hev1", "hvc1"):
            aliases.update(["hevc", "h265", "hev1", "hvc1"])
        elif c_lower in ("vp9"):
            aliases.update(["vp9"])
        elif c_lower in ("av1", "av01"):
            aliases.update(["av1", "av01"])

        supported = any(c.lower() in aliases for c in self.video_codecs)
        if not supported:
            return False

        if profile and self.video_profiles:
            p_lower = profile.lower()
            return any(p.lower() in p_lower or p_lower in p.lower() for p in self.video_profiles)
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
        hls_fmp4=True,
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
        hls_fmp4=True,
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
        hls_fmp4=True,
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
        hls_fmp4=True,
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
        hls_fmp4=False,
    ),
}


def resolve_client_capabilities(client: ClientCapabilities | None = None) -> ClientCapabilities:
    """Resolve preset and apply any client-provided overrides."""
    if client is None:
        return BUILTIN_PRESETS["unknown"].model_copy(deep=True)

    preset_name = (client.preset or "").lower().strip()
    base = BUILTIN_PRESETS.get(preset_name, BUILTIN_PRESETS["unknown"]).model_copy(deep=True)

    # If preset was unknown but client provided explicit codecs/containers, start fresh
    if not preset_name and (client.containers or client.video_codecs or client.audio_codecs):
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
    if client.subtitle_formats:
        base.subtitle_formats = [s.lower().strip() for s in client.subtitle_formats]
    if client.max_width is not None:
        base.max_width = client.max_width
    if client.max_height is not None:
        base.max_height = client.max_height
    if client.max_bitrate is not None:
        base.max_bitrate = client.max_bitrate
    if client.hdr:
        base.hdr = True
    if client.hls_fmp4:
        base.hls_fmp4 = True
    if client.preset:
        base.preset = client.preset

    return base


__all__ = [
    "BUILTIN_PRESETS",
    "ClientCapabilities",
    "PresetName",
    "resolve_client_capabilities",
]
