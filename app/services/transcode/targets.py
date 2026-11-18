"""Transcode target selection.

Turns a client's ordered "produce this" codec preferences into concrete ffmpeg
encoder names, filtered by the server's own capability and by the codec sets
that are valid inside HLS. This is the negotiation step between the pure
playback decision (``decision.py``) and the command builder (``builder.py``).

The module is deliberately pure: no filesystem, clock or network access. Its
inputs are already-resolved client capabilities, cached server capabilities and
codec names from the stored probe.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.transcode.capabilities import (
    ServerTranscodeCapabilities,
    normalize_codec,
)
from app.services.transcode.profile import ClientCapabilities

# Codec families permitted in HLS. Centralized so selection and output cannot
# disagree about what the packager can carry.
HLS_VIDEO_CODECS: frozenset[str] = frozenset({"h264", "hevc", "vp9", "av1"})
HLS_AUDIO_CODECS: frozenset[str] = frozenset({"aac", "ac3", "eac3", "mp3"})

DEFAULT_VIDEO_CODEC = "h264"
DEFAULT_AUDIO_CODEC = "aac"

# Hardware encoding stays scoped to the Intel VA-API H.264 path (ADR 064).
# Adding another family here is a deliberate decision, not an accident of a
# client listing more codecs.
HARDWARE_VIDEO_FAMILIES: frozenset[str] = frozenset({"h264"})


@dataclass(frozen=True)
class TargetSelection:
    """The chosen encoder targets for a transcode session.

    ``video_codec`` and ``audio_codec`` hold ffmpeg encoder names (for example
    ``h264_vaapi`` or ``aac``), matching ``TranscodePlan``. The ``*_family``
    fields hold canonical codec names for diagnostics and playlist codec strings.
    """

    video_codec: str
    video_family: str
    audio_codec: str
    audio_family: str


def _choose(
    codecs: list[str],
    *,
    source: str | None,
    allowed: frozenset[str],
    default_family: str,
    server: ServerTranscodeCapabilities,
    allow_hardware: bool,
    hardware_families: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """Pick the best (family, encoder) pair from an ordered preference list.

    A codec already used by the source ranks first: copying or minimally
    re-packing it preserves quality and channel layout. Equal ranks keep the
    client's order, which is the documented precedence rule.
    """
    source_family = normalize_codec(source) if source else None
    best: tuple[str, str] | None = None
    best_rank = 2

    for raw in codecs:
        family = normalize_codec(raw)
        if family not in allowed:
            continue
        family_hardware = allow_hardware and family in hardware_families
        encoder = server.encoder_for(family, allow_hardware=family_hardware)
        if encoder is None:
            continue
        rank = 0 if source_family is not None and family == source_family else 1
        if rank < best_rank:
            best_rank = rank
            best = (family, encoder)

    if best is not None:
        return best

    # No client preference survived the gates. Falling back to the H.264/AAC
    # defaults preserves Hearth's existing behaviour; Phase 2 will report this
    # case explicitly instead of silently substituting a codec.
    encoder = server.encoder_for(
        default_family,
        allow_hardware=allow_hardware and default_family in hardware_families,
    )
    return default_family, encoder or default_family


def select_transcode_targets(
    client: ClientCapabilities,
    server: ServerTranscodeCapabilities,
    *,
    source_video_codec: str | None = None,
    source_audio_codec: str | None = None,
    allow_video_hardware: bool = True,
) -> TargetSelection:
    """Choose video and audio encoder targets for a transcode session.

    ``allow_video_hardware`` is computed by the caller from source decode
    support and tone-mapping availability: a device that can encode H.264 is
    not automatically usable for a given source file.
    """
    video_family, video_encoder = _choose(
        client.video_transcode_codecs,
        source=source_video_codec,
        allowed=HLS_VIDEO_CODECS,
        default_family=DEFAULT_VIDEO_CODEC,
        server=server,
        allow_hardware=allow_video_hardware,
        hardware_families=HARDWARE_VIDEO_FAMILIES,
    )
    audio_family, audio_encoder = _choose(
        client.audio_transcode_codecs,
        source=source_audio_codec,
        allowed=HLS_AUDIO_CODECS,
        default_family=DEFAULT_AUDIO_CODEC,
        server=server,
        allow_hardware=False,
    )
    return TargetSelection(
        video_codec=video_encoder,
        video_family=video_family,
        audio_codec=audio_encoder,
        audio_family=audio_family,
    )


__all__ = [
    "HARDWARE_VIDEO_FAMILIES",
    "HLS_AUDIO_CODECS",
    "HLS_VIDEO_CODECS",
    "TargetSelection",
    "select_transcode_targets",
]
