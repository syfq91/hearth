"""Transcoding subsystem."""

from app.services.transcode.builder import build_ffmpeg_hls_command
from app.services.transcode.capabilities import (
    CodecCapability,
    ServerTranscodeCapabilities,
    parse_vainfo_output,
    probe_transcode_capabilities,
    reset_transcode_capabilities_cache,
)
from app.services.transcode.decision import (
    REMUX_ELIGIBLE_REASONS,
    ActionType,
    AudioCodec,
    Container,
    HwAccelMode,
    PlaybackMode,
    PlaybackOptions,
    SubtitleAction,
    TranscodePlan,
    TranscodeReason,
    UserLimits,
    budget_audio_bitrate,
    evaluate_playback_plan,
)
from app.services.transcode.profile import (
    BUILTIN_PRESETS,
    ClientCapabilities,
    PresetName,
    resolve_client_capabilities,
)

__all__ = [
    "ActionType",
    "AudioCodec",
    "BUILTIN_PRESETS",
    "ClientCapabilities",
    "CodecCapability",
    "Container",
    "HwAccelMode",
    "PlaybackMode",
    "PlaybackOptions",
    "PresetName",
    "REMUX_ELIGIBLE_REASONS",
    "ServerTranscodeCapabilities",
    "SubtitleAction",
    "TranscodePlan",
    "TranscodeReason",
    "UserLimits",
    "budget_audio_bitrate",
    "build_ffmpeg_hls_command",
    "evaluate_playback_plan",
    "parse_vainfo_output",
    "probe_transcode_capabilities",
    "reset_transcode_capabilities_cache",
    "resolve_client_capabilities",
]
