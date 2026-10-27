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
    ActionType,
    HwAccelMode,
    PlaybackMode,
    PlaybackOptions,
    SubtitleAction,
    TranscodePlan,
    UserLimits,
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
    "BUILTIN_PRESETS",
    "ClientCapabilities",
    "CodecCapability",
    "HwAccelMode",
    "PlaybackMode",
    "PlaybackOptions",
    "PresetName",
    "ServerTranscodeCapabilities",
    "SubtitleAction",
    "TranscodePlan",
    "UserLimits",
    "build_ffmpeg_hls_command",
    "evaluate_playback_plan",
    "parse_vainfo_output",
    "probe_transcode_capabilities",
    "reset_transcode_capabilities_cache",
    "resolve_client_capabilities",
]
