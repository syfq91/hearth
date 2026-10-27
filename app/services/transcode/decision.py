"""Playback decision engine.

Pure function that analyzes file streams, client profile, user restrictions,
and server capabilities to produce an actionable TranscodePlan.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.services.media.probe import ProbeResult, ProbeStream
from app.services.transcode.capabilities import ServerTranscodeCapabilities
from app.services.transcode.profile import ClientCapabilities, resolve_client_capabilities

PlaybackMode = Literal["direct_play", "remux_hls", "transcode_hls"]
ActionType = Literal["copy", "transcode"]
SubtitleAction = Literal["none", "sidecar_vtt", "burn_in"]
HwAccelMode = Literal["vaapi", "none"]


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


def evaluate_playback_plan(
    file_streams: ProbeResult,
    capabilities: ClientCapabilities,
    user_limits: UserLimits,
    server_caps: ServerTranscodeCapabilities,
    options: PlaybackOptions | None = None,
) -> TranscodePlan:
    """Evaluate media file against client and server constraints to form a TranscodePlan."""
    opts = options or PlaybackOptions()
    resolved_client = resolve_client_capabilities(capabilities)
    reasons: list[str] = []

    primary_video = file_streams.primary_video

    # Check user transcode permissions first
    if not user_limits.allow_transcode:
        reasons.append("Transcode disallowed by user limits; attempting direct play")
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

    # 1. Resolve Audio Stream
    audio_stream: ProbeStream | None = None
    if opts.audio_track_index is not None:
        for s in file_streams.streams:
            if s.index == opts.audio_track_index and s.codec_type == "audio":
                audio_stream = s
                break
    if audio_stream is None:
        # Default or first audio stream
        audio_streams = file_streams.audio_streams
        for s in audio_streams:
            if s.is_default:
                audio_stream = s
                break
        if audio_stream is None and audio_streams:
            audio_stream = audio_streams[0]

    # 2. Resolve Subtitle Stream
    sub_stream: ProbeStream | None = None
    if opts.subtitle_track_index is not None:
        for s in file_streams.streams:
            if s.index == opts.subtitle_track_index and s.codec_type == "subtitle":
                sub_stream = s
                break

    # 3. Check Subtitle compatibility
    subtitle_action: SubtitleAction = "none"
    subtitle_index: int | None = None
    sub_forces_burn_in = False

    if sub_stream is not None:
        subtitle_index = sub_stream.index
        c_name = (sub_stream.codec_name or "").lower()
        if c_name in ("hdmv_pgs_subtitle", "dvd_subtitle", "vobsub", "pgs", "xsub"):
            subtitle_action = "burn_in"
            sub_forces_burn_in = True
            reasons.append(f"Bitmap subtitle format '{c_name}' requires burn-in overlay")
        elif c_name in ("ass", "ssa"):
            subtitle_action = "burn_in"
            sub_forces_burn_in = True
            reasons.append(f"Styled subtitle format '{c_name}' requires burn-in overlay")
        else:
            subtitle_action = "sidecar_vtt"
            reasons.append(f"Text subtitle format '{c_name}' served as sidecar WebVTT")

    # 4. Check Video Compatibility
    video_transcode_needed = sub_forces_burn_in
    tonemap_needed = False
    target_w: int | None = None
    target_h: int | None = None

    if primary_video is not None:
        v_codec = (primary_video.codec_name or "").lower()
        v_profile = primary_video.profile

        if not resolved_client.supports_video_codec(v_codec, v_profile):
            video_transcode_needed = True
            reasons.append(f"Video codec '{v_codec}' is not supported by client")

        if primary_video.is_hdr and not resolved_client.hdr:
            video_transcode_needed = True
            tonemap_needed = True
            reasons.append("HDR color transfer requires tonemapping to SDR")

        if primary_video.width and primary_video.height:
            src_w = primary_video.width
            src_h = primary_video.height
            max_w = resolved_client.max_width
            max_h = resolved_client.max_height

            scale_w = (max_w / src_w) if max_w and src_w > max_w else 1.0
            scale_h = (max_h / src_h) if max_h and src_h > max_h else 1.0
            scale = min(scale_w, scale_h)

            if scale < 1.0:
                video_transcode_needed = True
                calc_w = int(src_w * scale)
                calc_h = int(src_h * scale)
                # Keep even dimensions
                target_w = calc_w - (calc_w % 2)
                target_h = calc_h - (calc_h % 2)
                reasons.append(
                    f"Video resolution {src_w}x{src_h} exceeds client maximum; "
                    f"downscaling to {target_w}x{target_h}"
                )

    # Check bitrate limits
    bitrate_limits: list[int] = []
    if resolved_client.max_bitrate:
        bitrate_limits.append(resolved_client.max_bitrate)
    if user_limits.max_bitrate:
        bitrate_limits.append(user_limits.max_bitrate)
    if opts.target_max_bitrate:
        bitrate_limits.append(opts.target_max_bitrate)

    target_bitrate: int | None = min(bitrate_limits) if bitrate_limits else None
    if target_bitrate and file_streams.bit_rate and file_streams.bit_rate > target_bitrate:
        video_transcode_needed = True
        reasons.append(
            f"Bitrate {file_streams.bit_rate} exceeds limit {target_bitrate}; capping bitrate"
        )

    # 5. Check Audio Compatibility
    audio_transcode_needed = False
    audio_codec_target = "copy"
    target_channels = 2

    if audio_stream is not None:
        a_codec = (audio_stream.codec_name or "").lower()
        if not resolved_client.supports_audio_codec(a_codec):
            audio_transcode_needed = True
            audio_codec_target = "aac"
            reasons.append(
                f"Audio codec '{a_codec}' is not supported by client; transcoding to AAC"
            )
        elif audio_stream.channels and audio_stream.channels > 2:
            target_channels = audio_stream.channels
    else:
        audio_codec_target = "copy"

    # 6. Check Container Compatibility
    container_supported = resolved_client.supports_container(file_streams.container)
    if not container_supported:
        reasons.append(f"Container '{file_streams.container}' is not supported natively by client")

    # 7. Hardware vs Software Selection
    hwaccel_mode: HwAccelMode = "none"
    video_codec_target = "copy"

    if video_transcode_needed:
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
            reasons.append(
                f"VAAPI cannot decode '{primary_video.codec_name}'; falling back to software"
            )

        if tonemap_needed and not server_caps.tonemap_vaapi:
            can_vaapi = False
            reasons.append("Server lacks tonemap_vaapi support; falling back to software")

        if can_vaapi:
            hwaccel_mode = "vaapi"
            video_codec_target = "h264_vaapi"
            reasons.append("Using VAAPI hardware acceleration")
        else:
            hwaccel_mode = "none"
            video_codec_target = "libx264"
            reasons.append("Falling back to software x264 video transcode")

    # 8. Delivery Mode Selection
    if video_transcode_needed:
        mode: PlaybackMode = "transcode_hls"
        v_action: ActionType = "transcode"
        a_action: ActionType = "transcode" if audio_transcode_needed else "copy"
        if not audio_transcode_needed:
            audio_codec_target = "copy"
    elif audio_transcode_needed or not container_supported:
        mode = "remux_hls"
        v_action = "copy"
        video_codec_target = "copy"
        a_action = "transcode" if audio_transcode_needed else "copy"
    else:
        mode = "direct_play"
        v_action = "copy"
        video_codec_target = "copy"
        a_action = "copy"
        audio_codec_target = "copy"
        reasons.append(
            "Direct play: container, video, and audio are compatible with client profile"
        )

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
    "HwAccelMode",
    "PlaybackMode",
    "PlaybackOptions",
    "SubtitleAction",
    "TranscodePlan",
    "UserLimits",
    "evaluate_playback_plan",
]
