"""Dynamic RFC 6381 codec strings for HLS playlists (T08).

Implements video_codec_string and audio_codec_string per §3.6.
Enforces INV-2: unknown inputs -> omitted rather than fabricated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.transcode.session import TranscodeSession

# H.264 profile_idc mappings (ISO/IEC 14496-10 / RFC 6381)
_H264_PROFILES: dict[str, int] = {
    "baseline": 0x42,  # 66
    "constrained baseline": 0x42,
    "main": 0x4D,  # 77
    "extended": 0x58,  # 88
    "high": 0x64,  # 100
    "high 10": 0x6E,  # 110
    "high 4:2:2": 0x7A,  # 122
    "high 4:4:4": 0xF4,  # 244
    "high 4:4:4 predictive": 0xF4,
}

# HEVC profile indices per ISO/IEC 23008-2 / RFC 6381
_HEVC_PROFILES: dict[str, tuple[int, int]] = {
    "main": (1, 6),  # (profile_index, general_profile_compatibility_flags)
    "main 10": (2, 2),
    "main still picture": (3, 6),
}

# AV1 level index mapping per AV1 Codec ISO Media File Format Binding
_AV1_LEVEL_MAP: dict[str, int] = {
    "2.0": 0,
    "2.1": 1,
    "3.0": 4,
    "3.1": 5,
    "4.0": 8,
    "4.1": 9,
    "5.0": 12,
    "5.1": 13,
    "5.2": 14,
    "5.3": 15,
    "6.0": 16,
    "6.1": 17,
    "6.2": 18,
    "6.3": 19,
}


def _parse_h264_level(level: int | float | str | None) -> int | None:
    if level is None:
        return None
    try:
        if isinstance(level, str):
            level = float(level) if "." in level else int(level)
        if isinstance(level, float):
            return int(round(level * 10))
        if isinstance(level, int):
            if level < 10:
                return level * 10
            return level
    except (ValueError, TypeError):
        return None
    return None


def _parse_hevc_level(level: int | float | str | None) -> int | None:
    if level is None:
        return None
    try:
        if isinstance(level, str):
            s = level.strip().upper()
            if s.startswith("L") or s.startswith("H"):
                s = s[1:]
            if "." in s:
                return int(round(float(s) * 30))
            return int(s)
        if isinstance(level, float):
            return int(round(level * 30))
        if isinstance(level, int):
            return level
    except (ValueError, TypeError):
        return None
    return None


def video_codec_string(
    codec: str | None,
    profile: str | None = None,
    level: int | float | str | None = None,
    bit_depth: int | None = 8,
    constraint: str | int | None = None,
    tier: str | None = None,
) -> str | None:
    """Compute RFC 6381 video codec string.

    Returns None for unknown profiles, levels, or codecs (INV-2).
    """
    if not codec:
        return None

    c = codec.strip().lower()

    # --- H.264 / AVC ---
    if c in ("h264", "avc", "avc1", "libx264", "h264_vaapi"):
        if not profile:
            return None
        p_clean = profile.strip().lower()
        profile_idc = _H264_PROFILES.get(p_clean)
        if profile_idc is None:
            return None

        level_idc = _parse_h264_level(level)
        if level_idc is None:
            return None

        if constraint is not None:
            if isinstance(constraint, int):
                constraint_str = f"{constraint:02x}"
            else:
                constraint_str = constraint.strip().lower()
        else:
            constraint_str = "00"

        return f"avc1.{profile_idc:02x}{constraint_str}{level_idc:02x}"

    # --- HEVC / H.265 ---
    if c in ("hevc", "h265", "hvc1", "hevc_vaapi", "libx265"):
        if not profile:
            return None
        p_clean = profile.strip().lower()
        entry = _HEVC_PROFILES.get(p_clean)
        if entry is None:
            return None
        prof_idx, compat_flags = entry

        level_idc = _parse_hevc_level(level)
        if level_idc is None:
            return None

        t = "H" if tier and tier.strip().upper() in ("H", "HIGH") else "L"
        # Per §3.6: hvc1.<profile>.<compat flags>.<L|H><level_idc>.<constraints>
        return f"hvc1.{prof_idx}.{compat_flags}.{t}{level_idc}.B0"

    # --- AV1 ---
    if c in ("av1", "av01", "libaom-av1", "libsvtav1"):
        prof = 0
        if profile:
            p_clean = profile.strip().lower()
            if p_clean in ("main", "0"):
                prof = 0
            elif p_clean in ("high", "1"):
                prof = 1
            elif p_clean in ("professional", "2"):
                prof = 2
            else:
                return None

        level_idx = 8  # Default to Level 4.0
        if level is not None:
            if isinstance(level, (int, float)):
                key = f"{level:.1f}" if isinstance(level, float) else f"{level}"
                if key in _AV1_LEVEL_MAP:
                    level_idx = _AV1_LEVEL_MAP[key]
                elif isinstance(level, int):
                    level_idx = level
            elif isinstance(level, str):
                s = level.strip()
                if s in _AV1_LEVEL_MAP:
                    level_idx = _AV1_LEVEL_MAP[s]
                elif s.isdigit():
                    level_idx = int(s)

        t = "H" if tier and tier.strip().upper() in ("H", "HIGH") else "M"
        depth = 8
        if bit_depth is not None:
            try:
                depth = int(bit_depth)
            except (ValueError, TypeError):
                depth = 8

        return f"av01.{prof}.{level_idx:02d}{t}.{depth:02d}"

    return None


def audio_codec_string(
    codec: str | None,
    profile: str | None = None,
) -> str | None:
    """Compute RFC 6381 audio codec string.

    Returns None for unknown profiles or codecs (INV-2).
    """
    if not codec:
        return None

    c = codec.strip().lower()

    # --- AAC ---
    if c in ("aac", "mp4a"):
        if profile is None:
            return "mp4a.40.2"
        p_clean = profile.strip().lower()
        if p_clean in ("lc", "aac-lc", "aac_low", "low"):
            return "mp4a.40.2"
        if p_clean in ("he-aac", "he-aacv1", "he_aac", "he-aac v1"):
            return "mp4a.40.5"
        if p_clean in ("he-aacv2", "he-aac v2"):
            return "mp4a.40.29"
        return None

    # --- AC-3 ---
    if c in ("ac3", "ac-3"):
        return "ac-3"

    # --- E-AC-3 ---
    if c in ("eac3", "ec-3", "e-ac-3"):
        return "ec-3"

    # --- Opus ---
    if c in ("opus",):
        return "Opus"

    return None


def compute_master_codecs(session: TranscodeSession) -> str | None:
    """Determine dynamic RFC 6381 CODECS attribute for master.m3u8."""
    video_str: str | None = None
    audio_str: str | None = None

    plan = session.plan

    # 1. Video stream
    if plan.video_action == "transcode":
        # Hearth transcode output is always H.264 High Profile Level 4.0
        video_str = video_codec_string("h264", profile="High", level=4.0)
    elif plan.video_action == "copy" and session.source_video:
        video_str = video_codec_string(
            session.source_video.codec_name,
            profile=session.source_video.profile,
            level=session.source_video.level,
        )

    # 2. Audio stream
    if plan.audio_action == "transcode":
        # Hearth audio transcode target is AAC-LC
        audio_str = audio_codec_string("aac", profile="LC")
    elif plan.audio_action == "copy" and session.source_audio:
        audio_str = audio_codec_string(
            session.source_audio.codec_name,
            profile=session.source_audio.profile,
        )

    parts = [s for s in (video_str, audio_str) if s]
    return ",".join(parts) if parts else None


__all__ = [
    "audio_codec_string",
    "compute_master_codecs",
    "video_codec_string",
]
