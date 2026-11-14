"""Playback decision engine.

Pure function that analyzes file streams, client profile, user restrictions,
and server capabilities to produce an actionable TranscodePlan.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.services.media.probe import ProbeResult, ProbeStream
from app.services.transcode.capabilities import ServerTranscodeCapabilities
from app.services.transcode.profile import ClientCapabilities, resolve_client_capabilities

PlaybackMode = Literal["direct_play", "remux_hls", "transcode_hls"]
ActionType = Literal["copy", "transcode"]
SubtitleAction = Literal["none", "sidecar_vtt", "burn_in", "external_ass"]
HwAccelMode = Literal["vaapi", "none"]


class TranscodeReason(StrEnum):
    """Reason why direct play is not possible per §3.3."""

    ContainerNotSupported = "ContainerNotSupported"
    AudioCodecNotSupported = "AudioCodecNotSupported"
    AudioChannelsNotSupported = "AudioChannelsNotSupported"
    AudioSampleRateNotSupported = "AudioSampleRateNotSupported"
    VideoCodecNotSupported = "VideoCodecNotSupported"
    VideoProfileNotSupported = "VideoProfileNotSupported"
    VideoLevelNotSupported = "VideoLevelNotSupported"
    VideoResolutionNotSupported = "VideoResolutionNotSupported"
    ContainerBitrateExceedsLimit = "ContainerBitrateExceedsLimit"
    VideoRangeTypeNotSupported = "VideoRangeTypeNotSupported"
    SubtitleBurnInRequired = "SubtitleBurnInRequired"


# Aliases matching §3.3 acceptance criteria
Container = TranscodeReason.ContainerNotSupported
AudioCodec = TranscodeReason.AudioCodecNotSupported

REMUX_ELIGIBLE_REASONS: frozenset[str] = frozenset(
    {
        TranscodeReason.ContainerNotSupported,
        TranscodeReason.AudioCodecNotSupported,
        TranscodeReason.AudioChannelsNotSupported,
        TranscodeReason.AudioSampleRateNotSupported,
    }
)


def budget_audio_bitrate(total_cap_bps: int) -> int:
    """Compute target audio bitrate from total bitrate cap per §3.4."""
    if total_cap_bps <= 640_000:
        return 128_000
    if total_cap_bps <= 2_000_000:
        return 192_000
    if total_cap_bps <= 10_000_000:
        return 384_000
    return 640_000


class UserLimits(BaseModel):
    """User-specific playback constraints."""

    max_bitrate: int | None = None
    allow_transcode: bool = True


class PlaybackOptions(BaseModel):
    """Client request overrides."""

    audio_track_index: int | None = None
    subtitle_track_index: int | None = None
    target_max_bitrate: int | None = None


class TranscodePlan(BaseModel):
    """Detailed plan produced by the decision engine."""

    mode: PlaybackMode
    video_action: ActionType
    video_codec: str
    audio_action: ActionType
    audio_codec: str
    audio_channels: int
    audio_stream_index: int | None = None
    subtitle_action: SubtitleAction
    subtitle_stream_index: int | None = None
    target_width: int | None = None
    target_height: int | None = None
    target_bitrate: int | None = None
    hwaccel: HwAccelMode
    tonemap_hdr: bool = False
    reasons: list[str] = Field(default_factory=list)


def _add_reason(reasons: list[str], reason: str) -> None:
    """Append a transcode reason if not already present, preserving order."""
    if reason not in reasons:
        reasons.append(reason)


def evaluate_playback_plan(
    file_streams: ProbeResult,
    capabilities: ClientCapabilities | None,
    user_limits: UserLimits,
    server_caps: ServerTranscodeCapabilities,
    options: PlaybackOptions | None = None,
    user_agent: str | None = None,
) -> TranscodePlan:
    """Evaluate media file against client and server constraints to form a TranscodePlan.

    Pure function adhering to INV-3 (no filesystem, clock, or network access).
    Computes the full union of failure reasons per §3.3 before selecting the delivery mode.
    """
    opts = options or PlaybackOptions()
    resolved_client = resolve_client_capabilities(capabilities, user_agent=user_agent)
    reasons: list[str] = []

    primary_video = file_streams.primary_video

    # 1. Resolve Audio Stream
    audio_stream: ProbeStream | None = None
    if opts.audio_track_index is not None:
        for s in file_streams.streams:
            if s.index == opts.audio_track_index and s.codec_type == "audio":
                audio_stream = s
                break
    if audio_stream is None:
        audio_streams = file_streams.audio_streams
        for s in audio_streams:
            if s.is_default:
                audio_stream = s
                break
        if audio_stream is None and audio_streams:
            audio_stream = audio_streams[0]

    # 2. Resolve Subtitle Stream
    sub_stream: ProbeStream | None = None
    sub_stream_index: int | None = None
    if opts.subtitle_track_index is not None:
        sub_counter = 0
        for s in file_streams.streams:
            if s.codec_type == "subtitle":
                if s.index == opts.subtitle_track_index:
                    sub_stream = s
                    sub_stream_index = sub_counter
                    break
                sub_counter += 1

    # 3. Check Subtitle compatibility
    subtitle_action: SubtitleAction = "none"
    subtitle_index: int | None = None

    if sub_stream is not None:
        subtitle_index = sub_stream_index
        c_name = (sub_stream.codec_name or "").lower()
        bitmap_codecs = frozenset(
            {"hdmv_pgs_subtitle", "dvd_subtitle", "dvdsub", "vobsub", "pgs", "pgssub", "xsub"}
        )

        if c_name in bitmap_codecs:
            subtitle_action = "burn_in"
            _add_reason(reasons, TranscodeReason.SubtitleBurnInRequired)
        else:
            # Text subtitle (SRT, ASS, SSA, WebVTT, etc.)
            c_norm = (
                "srt"
                if c_name in ("subrip", "srt")
                else ("vtt" if c_name in ("webvtt", "vtt") else c_name)
            )

            matched_profile = None
            vtt_profile = None
            if resolved_client.subtitle_profiles:
                for prof in resolved_client.subtitle_profiles:
                    p_fmt = prof.format.lower().lstrip(".")
                    if p_fmt in (c_name, c_norm):
                        matched_profile = prof
                    if p_fmt in ("vtt", "webvtt"):
                        vtt_profile = prof

            if matched_profile is not None:
                m = matched_profile.method.lower()
                if m == "encode":
                    subtitle_action = "burn_in"
                    _add_reason(reasons, TranscodeReason.SubtitleBurnInRequired)
                elif m == "external" and c_norm in ("ass", "ssa"):
                    subtitle_action = "external_ass"
                else:
                    subtitle_action = "sidecar_vtt"
            elif vtt_profile is not None:
                m = vtt_profile.method.lower()
                if m == "encode":
                    subtitle_action = "burn_in"
                    _add_reason(reasons, TranscodeReason.SubtitleBurnInRequired)
                else:
                    subtitle_action = "sidecar_vtt"
            elif resolved_client.subtitle_profiles and not resolved_client.subtitle_formats:
                # Client supplied subtitle_profiles but none matched text/vtt delivery
                subtitle_action = "burn_in"
                _add_reason(reasons, TranscodeReason.SubtitleBurnInRequired)
            elif resolved_client.supports_subtitle("vtt") or resolved_client.supports_subtitle(
                c_norm
            ):
                subtitle_action = "sidecar_vtt"
            else:
                subtitle_action = "burn_in"
                _add_reason(reasons, TranscodeReason.SubtitleBurnInRequired)

    # 4. Check Video Compatibility
    tonemap_needed = False
    target_w: int | None = None
    target_h: int | None = None

    if primary_video is not None:
        v_codec = (primary_video.codec_name or "").lower()
        v_profile = primary_video.profile

        if not resolved_client.supports_video_codec(v_codec):
            _add_reason(reasons, TranscodeReason.VideoCodecNotSupported)
        elif v_profile and not resolved_client.supports_video_codec(v_codec, v_profile):
            _add_reason(reasons, TranscodeReason.VideoProfileNotSupported)

        if (
            resolved_client.max_video_level is not None
            and primary_video.level is not None
            and primary_video.level > resolved_client.max_video_level
        ):
            _add_reason(reasons, TranscodeReason.VideoLevelNotSupported)

        if primary_video.is_hdr and not resolved_client.hdr:
            tonemap_needed = True
            _add_reason(reasons, TranscodeReason.VideoRangeTypeNotSupported)

        if primary_video.width and primary_video.height:
            src_w = primary_video.width
            src_h = primary_video.height
            max_w = resolved_client.max_width
            max_h = resolved_client.max_height

            scale_w = (max_w / src_w) if max_w and src_w > max_w else 1.0
            scale_h = (max_h / src_h) if max_h and src_h > max_h else 1.0
            scale = min(scale_w, scale_h)

            if scale < 1.0:
                calc_w = int(src_w * scale)
                calc_h = int(src_h * scale)
                target_w = calc_w - (calc_w % 2)
                target_h = calc_h - (calc_h % 2)
                _add_reason(reasons, TranscodeReason.VideoResolutionNotSupported)

    # Check bitrate limits (INV-2: probe values stored as null;
    # 40 Mbps assumed at decision time per §3.1)
    bitrate_limits: list[int] = []
    if resolved_client.max_bitrate:
        bitrate_limits.append(resolved_client.max_bitrate)
    if user_limits.max_bitrate:
        bitrate_limits.append(user_limits.max_bitrate)
    if opts.target_max_bitrate:
        bitrate_limits.append(opts.target_max_bitrate)

    target_bitrate: int | None = min(bitrate_limits) if bitrate_limits else None
    if file_streams.bit_rate is not None:
        effective_bitrate = file_streams.bit_rate
    elif (
        file_streams.size_bytes
        and file_streams.duration_seconds
        and file_streams.duration_seconds > 0
    ):
        effective_bitrate = int((file_streams.size_bytes * 8) / file_streams.duration_seconds)
    else:
        effective_bitrate = 40_000_000
    if target_bitrate and effective_bitrate > target_bitrate:
        _add_reason(reasons, TranscodeReason.ContainerBitrateExceedsLimit)

    # 5. Check Audio Compatibility
    audio_transcode_needed = False
    audio_codec_target = "copy"
    target_channels = 2

    if audio_stream is not None:
        a_codec = (audio_stream.codec_name or "").lower()
        if not resolved_client.supports_audio_codec(a_codec):
            audio_transcode_needed = True
            audio_codec_target = "aac"
            _add_reason(reasons, TranscodeReason.AudioCodecNotSupported)
        if (
            resolved_client.max_audio_channels is not None
            and audio_stream.channels is not None
            and audio_stream.channels > resolved_client.max_audio_channels
        ):
            audio_transcode_needed = True
            audio_codec_target = "aac"
            _add_reason(reasons, TranscodeReason.AudioChannelsNotSupported)
        if (
            resolved_client.max_audio_sample_rate is not None
            and audio_stream.sample_rate is not None
            and audio_stream.sample_rate > resolved_client.max_audio_sample_rate
        ):
            audio_transcode_needed = True
            audio_codec_target = "aac"
            _add_reason(reasons, TranscodeReason.AudioSampleRateNotSupported)

        if audio_stream.channels and audio_stream.channels > 2:
            target_channels = audio_stream.channels
    else:
        audio_codec_target = "copy"

    # 6. Check Container Compatibility
    container_supported = resolved_client.supports_container(file_streams.container)
    if not container_supported:
        _add_reason(reasons, TranscodeReason.ContainerNotSupported)

    # 7. Remux Rule (§3.3): remux is allowed iff reason set is subset of REMUX_ELIGIBLE_REASONS.
    # Empty set means DirectPlay.
    reason_set = set(reasons)
    if not reason_set:
        mode: PlaybackMode = "direct_play"
    elif reason_set.issubset(REMUX_ELIGIBLE_REASONS):
        mode = "remux_hls"
    else:
        mode = "transcode_hls"

    # User transcode permission override
    if not user_limits.allow_transcode and mode != "direct_play":
        return TranscodePlan(
            mode="direct_play",
            video_action="copy",
            video_codec="copy",
            audio_action="copy",
            audio_codec="copy",
            audio_channels=2,
            subtitle_action="none",
            hwaccel="none",
            reasons=reasons,
        )

    # 8. Action types, codecs, and hardware vs software selection
    hwaccel_mode: HwAccelMode = "none"
    video_codec_target = "copy"

    if mode == "direct_play":
        v_action: ActionType = "copy"
        video_codec_target = "copy"
        a_action: ActionType = "copy"
        audio_codec_target = "copy"
    elif mode == "remux_hls":
        v_action = "copy"
        video_codec_target = "copy"
        a_action = "transcode" if audio_transcode_needed else "copy"
    else:  # transcode_hls
        v_action = "transcode"
        a_action = "transcode" if audio_transcode_needed else "copy"

        can_vaapi = (
            server_caps.available
            and not server_caps.force_software
            and server_caps.can_vaapi_encode("h264")
        )
        if (
            primary_video
            and primary_video.codec_name
            and not server_caps.can_vaapi_decode(primary_video.codec_name)
        ):
            can_vaapi = False

        if tonemap_needed and not server_caps.tonemap_vaapi:
            can_vaapi = False

        if can_vaapi:
            hwaccel_mode = "vaapi"
            video_codec_target = "h264_vaapi"
        else:
            hwaccel_mode = "none"
            video_codec_target = "libx264"

    return TranscodePlan(
        mode=mode,
        video_action=v_action,
        video_codec=video_codec_target,
        audio_action=a_action,
        audio_codec=audio_codec_target,
        audio_channels=target_channels,
        audio_stream_index=audio_stream.index if audio_stream else None,
        subtitle_action=subtitle_action,
        subtitle_stream_index=subtitle_index,
        target_width=target_w,
        target_height=target_h,
        target_bitrate=target_bitrate,
        hwaccel=hwaccel_mode,
        tonemap_hdr=tonemap_needed,
        reasons=reasons,
    )


__all__ = [
    "ActionType",
    "AudioCodec",
    "Container",
    "HwAccelMode",
    "PlaybackMode",
    "PlaybackOptions",
    "REMUX_ELIGIBLE_REASONS",
    "SubtitleAction",
    "TranscodePlan",
    "TranscodeReason",
    "UserLimits",
    "budget_audio_bitrate",
    "evaluate_playback_plan",
]
