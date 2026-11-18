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
from app.services.transcode.profile import ClientCapabilities, TranscodeTarget

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

# Hearth packages video as fMP4 HLS; no other container target can be produced.
FMP4_CONTAINERS: frozenset[str] = frozenset({"mp4", "fmp4", "m4s"})


@dataclass(frozen=True)
class TargetConstraints:
    """Typed ceilings from the chosen target, used to shape encoder output."""

    max_width: int | None = None
    max_height: int | None = None
    max_bitrate: int | None = None
    max_video_level: int | None = None
    max_audio_channels: int | None = None


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
    constraints: TargetConstraints | None = None
    # True when no client target could be honoured and the conservative
    # H.264/AAC fallback was used. Callers surface this as a diagnostic reason.
    fallback_used: bool = False
    target_index: int | None = None


def _has_usable_codec(
    codecs: list[str],
    *,
    allowed: frozenset[str],
    server: ServerTranscodeCapabilities,
    allow_hardware: bool,
    hardware_families: frozenset[str] = frozenset(),
) -> bool:
    """Whether at least one codec survives the HLS and encoder gates."""
    return any(
        normalize_codec(codec) in allowed
        and server.encoder_for(
            normalize_codec(codec),
            allow_hardware=allow_hardware and normalize_codec(codec) in hardware_families,
        )
        is not None
        for codec in codecs
    )


def _choose(
    codecs: list[str],
    *,
    source: str | None,
    allowed: frozenset[str],
    default_family: str,
    server: ServerTranscodeCapabilities,
    allow_hardware: bool,
    hardware_families: frozenset[str] = frozenset(),
) -> tuple[str, str, bool]:
    """Pick the best (family, encoder) pair from an ordered preference list.

    A codec already used by the source ranks first: copying or minimally
    re-packing it preserves quality and channel layout. Equal ranks keep the
    client's order. The final flag reports that the default had to be used.
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
        return best[0], best[1], False

    encoder = server.encoder_for(
        default_family,
        allow_hardware=allow_hardware and default_family in hardware_families,
    )
    return default_family, encoder or default_family, True


def _rank_target(
    target: TranscodeTarget,
    *,
    source_video_codec: str | None,
    source_audio_codec: str | None,
    source_width: int | None,
    source_height: int | None,
) -> tuple[int, int]:
    """Rank an applicable target: (video copy potential, audio match).

    A source-matching video codec ranks first, unless the target's own
    resolution ceiling forces a downscale. A source-matching audio codec ranks
    first so its channel layout survives. Lower is better.
    """
    video_rank = 1
    if source_video_codec:
        source_family = normalize_codec(source_video_codec)
        if any(normalize_codec(c) == source_family for c in target.video_codecs):
            video_rank = 0
            exceeds_width = bool(
                source_width and target.max_width and source_width > target.max_width
            )
            exceeds_height = bool(
                source_height and target.max_height and source_height > target.max_height
            )
            if exceeds_width or exceeds_height:
                video_rank = 1

    audio_rank = 1
    if source_audio_codec:
        source_family = normalize_codec(source_audio_codec)
        if any(normalize_codec(c) == source_family for c in target.audio_codecs):
            audio_rank = 0
    return video_rank, audio_rank


def _constraints_from(target: TranscodeTarget) -> TargetConstraints:
    return TargetConstraints(
        max_width=target.max_width,
        max_height=target.max_height,
        max_bitrate=target.max_bitrate,
        max_video_level=target.max_video_level,
        max_audio_channels=target.max_audio_channels,
    )


def select_transcode_targets(
    client: ClientCapabilities,
    server: ServerTranscodeCapabilities,
    *,
    source_video_codec: str | None = None,
    source_audio_codec: str | None = None,
    source_width: int | None = None,
    source_height: int | None = None,
    allow_video_hardware: bool = True,
) -> TargetSelection:
    """Choose encoder targets for a transcode session.

    Stage one discards conditional targets whose container cannot be produced
    or whose codecs the server cannot encode. Stage two ranks the remaining
    targets by copy/match potential and keeps their order as the tie-breaker.
    The chosen target's ceilings are returned for the caller to apply.
    """
    chosen_target: TranscodeTarget | None = None
    target_index: int | None = None
    fallback_used = False

    if client.transcode_targets:
        candidates: list[tuple[int, TranscodeTarget]] = []
        for index, target in enumerate(client.transcode_targets):
            if target.container is not None and target.container not in FMP4_CONTAINERS:
                continue
            video_ok = _has_usable_codec(
                target.video_codecs,
                allowed=HLS_VIDEO_CODECS,
                server=server,
                allow_hardware=allow_video_hardware,
                hardware_families=HARDWARE_VIDEO_FAMILIES,
            )
            audio_ok = _has_usable_codec(
                target.audio_codecs,
                allowed=HLS_AUDIO_CODECS,
                server=server,
                allow_hardware=False,
            )
            if video_ok or audio_ok:
                candidates.append((index, target))

        if candidates:
            candidates.sort(
                key=lambda item: (
                    *_rank_target(
                        item[1],
                        source_video_codec=source_video_codec,
                        source_audio_codec=source_audio_codec,
                        source_width=source_width,
                        source_height=source_height,
                    ),
                    item[0],
                )
            )
            target_index, chosen_target = candidates[0]
        else:
            # Every requested target was rejected: report the fallback instead
            # of silently substituting another codec.
            fallback_used = True

    video_codecs = chosen_target.video_codecs if chosen_target else client.video_transcode_codecs
    audio_codecs = chosen_target.audio_codecs if chosen_target else client.audio_transcode_codecs
    video_family, video_encoder, video_default = _choose(
        video_codecs,
        source=source_video_codec,
        allowed=HLS_VIDEO_CODECS,
        default_family=DEFAULT_VIDEO_CODEC,
        server=server,
        allow_hardware=allow_video_hardware,
        hardware_families=HARDWARE_VIDEO_FAMILIES,
    )
    audio_family, audio_encoder, audio_default = _choose(
        audio_codecs,
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
        constraints=_constraints_from(chosen_target) if chosen_target else None,
        fallback_used=fallback_used or video_default or audio_default,
        target_index=target_index,
    )


__all__ = [
    "FMP4_CONTAINERS",
    "HARDWARE_VIDEO_FAMILIES",
    "HLS_AUDIO_CODECS",
    "HLS_VIDEO_CODECS",
    "TargetConstraints",
    "TargetSelection",
    "select_transcode_targets",
]
