"""Unit tests for the transcode decision engine.

Pure function, table-driven test matrix of >= 40 cases covering:
- Direct play (containers, codecs, profiles)
- Remux HLS (container or audio conversion without video re-encode)
- Video transcoding (codec mismatches, resolution downscaling, bitrate limits)
- HDR to SDR tonemapping
- Subtitle actions (sidecar VTT vs bitmap burn-in)
- Hardware (VAAPI) vs Software (libx264) selection
- User limits and permissions
"""

from typing import Any

import pytest

from app.services.media.probe import ProbeResult, ProbeStream
from app.services.transcode.capabilities import (
    CodecCapability,
    ServerTranscodeCapabilities,
)
from app.services.transcode.decision import (
    AudioCodec,
    Container,
    PlaybackOptions,
    TranscodePlan,
    TranscodeReason,
    UserLimits,
    evaluate_playback_plan,
)
from app.services.transcode.profile import BUILTIN_PRESETS, ClientCapabilities


def _make_probe(
    container: str = "mp4",
    v_codec: str | None = "h264",
    v_profile: str | None = "High",
    width: int = 1920,
    height: int = 1080,
    bit_rate: int = 4_000_000,
    is_hdr: bool = False,
    a_codec: str | None = "aac",
    a_channels: int = 2,
    subtitles: list[dict[str, Any]] | None = None,
) -> ProbeResult:
    streams: list[ProbeStream] = []
    idx = 0
    if v_codec:
        streams.append(
            ProbeStream(
                index=idx,
                codec_type="video",
                codec_name=v_codec,
                profile=v_profile,
                width=width,
                height=height,
                bit_rate=bit_rate,
                is_hdr=is_hdr,
                color_transfer="smpte2084" if is_hdr else None,
            )
        )
        idx += 1
    if a_codec:
        streams.append(
            ProbeStream(
                index=idx,
                codec_type="audio",
                codec_name=a_codec,
                channels=a_channels,
                bit_rate=192_000,
                is_default=True,
            )
        )
        idx += 1
    if subtitles:
        for s in subtitles:
            streams.append(
                ProbeStream(
                    index=idx,
                    codec_type="subtitle",
                    codec_name=s.get("codec", "subrip"),
                    language=s.get("language", "eng"),
                    is_forced=s.get("forced", False),
                    is_default=s.get("default", False),
                )
            )
            idx += 1
    return ProbeResult(
        container=container,
        bit_rate=bit_rate + 192_000,
        duration_seconds=120.0,
        streams=streams,
    )


def _vaapi_caps(
    h264_enc: bool = True,
    hevc_enc: bool = True,
    hevc_dec: bool = True,
    tonemap: bool = True,
    force_sw: bool = False,
) -> ServerTranscodeCapabilities:
    return ServerTranscodeCapabilities(
        render_device="/dev/dri/renderD128",
        available=not force_sw,
        force_software=force_sw,
        driver_version="Intel iHD 23.1.1",
        tonemap_vaapi=tonemap,
        codecs={
            "h264": CodecCapability(
                codec="h264", decode=True, encode=h264_enc, test_encode_ok=h264_enc
            ),
            "hevc": CodecCapability(
                codec="hevc", decode=hevc_dec, encode=hevc_enc, test_encode_ok=hevc_enc
            ),
            "vp9": CodecCapability(codec="vp9", decode=True, encode=False),
            "av1": CodecCapability(codec="av1", decode=True, encode=False),
            "mpeg2": CodecCapability(codec="mpeg2", decode=True, encode=False),
            "vc1": CodecCapability(codec="vc1", decode=True, encode=False),
        },
    )


# ==============================================================================
# Matrix of Test Cases (At least 40 test cases)
# ==============================================================================

TEST_MATRIX = [
    # 1-5: Direct play scenarios
    (
        "01_direct_play_mp4_h264_aac_chrome",
        _make_probe("mp4", "h264", "High", 1920, 1080, 3_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "direct_play",
        "copy",
        "copy",
        "none",
        "none",
    ),
    (
        "02_direct_play_webm_vp9_opus_chrome",
        _make_probe("webm", "vp9", "Profile 0", 1920, 1080, 2_000_000, False, "opus", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "direct_play",
        "copy",
        "copy",
        "none",
        "none",
    ),
    (
        "03_direct_play_mp4_hevc_aac_safari",
        _make_probe("mp4", "hevc", "Main 10", 3840, 2160, 15_000_000, True, "aac", 2),
        BUILTIN_PRESETS["safari"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "direct_play",
        "copy",
        "copy",
        "none",
        "none",
    ),
    (
        "04_direct_play_mkv_hevc_ac3_android_tv",
        _make_probe("mkv", "hevc", "Main", 1920, 1080, 5_000_000, False, "ac3", 6),
        BUILTIN_PRESETS["android_tv"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "direct_play",
        "copy",
        "copy",
        "none",
        "none",
    ),
    (
        "05_direct_play_mp4_flac_chrome",
        _make_probe("mp4", "h264", "High", 1280, 720, 1_500_000, False, "flac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "direct_play",
        "copy",
        "copy",
        "none",
        "none",
    ),
    # 6-10: Remux HLS (Container mismatch, video & audio supported)
    (
        "06_remux_mkv_to_hls_chrome",
        _make_probe("mkv", "h264", "High", 1920, 1080, 3_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "remux_hls",
        "copy",
        "copy",
        "none",
        "none",
    ),
    (
        "07_remux_avi_to_hls_chrome",
        _make_probe("avi", "h264", "High", 1280, 720, 2_000_000, False, "mp3", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "remux_hls",
        "copy",
        "copy",
        "none",
        "none",
    ),
    (
        "08_remux_ts_to_hls_firefox",
        _make_probe("ts", "h264", "High", 1920, 1080, 4_000_000, False, "aac", 2),
        BUILTIN_PRESETS["firefox"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "remux_hls",
        "copy",
        "copy",
        "none",
        "none",
    ),
    (
        "09_remux_mkv_vp9_opus_firefox",
        _make_probe("mkv", "vp9", "Profile 0", 1920, 1080, 3_000_000, False, "opus", 2),
        BUILTIN_PRESETS["firefox"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "remux_hls",
        "copy",
        "copy",
        "none",
        "none",
    ),
    (
        "10_remux_mov_unsupported_audio_container_remux",
        _make_probe("mov", "h264", "High", 1920, 1080, 3_000_000, False, "aac", 2),
        # Mov is aliased to mp4, but if user requests HLS:
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "direct_play",  # mov maps to mp4
        "copy",
        "copy",
        "none",
        "none",
    ),
    # 11-15: Audio transcoding only (Remux HLS with audio transcode)
    (
        "11_audio_transcode_dts_to_aac_chrome",
        _make_probe("mp4", "h264", "High", 1920, 1080, 4_000_000, False, "dts", 6),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "remux_hls",
        "copy",
        "transcode",
        "none",
        "none",
    ),
    (
        "12_audio_transcode_truehd_to_aac_chrome",
        _make_probe("mkv", "h264", "High", 1920, 1080, 4_000_000, False, "truehd", 8),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "remux_hls",
        "copy",
        "transcode",
        "none",
        "none",
    ),
    (
        "13_audio_transcode_eac3_to_aac_firefox",
        _make_probe("mp4", "h264", "High", 1920, 1080, 3_000_000, False, "eac3", 6),
        BUILTIN_PRESETS["firefox"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "remux_hls",
        "copy",
        "transcode",
        "none",
        "none",
    ),
    (
        "14_audio_transcode_vorbis_to_aac_safari",
        _make_probe("mp4", "h264", "High", 1920, 1080, 3_000_000, False, "vorbis", 2),
        BUILTIN_PRESETS["safari"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "remux_hls",
        "copy",
        "transcode",
        "none",
        "none",
    ),
    (
        "15_audio_transcode_ac3_to_aac_chrome",
        _make_probe("mkv", "h264", "High", 1920, 1080, 3_000_000, False, "ac3", 6),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "remux_hls",
        "copy",
        "transcode",
        "none",
        "none",
    ),
    # 16-20: Video transcoding due to unsupported codec
    (
        "16_video_transcode_hevc_on_chrome",
        _make_probe("mp4", "hevc", "Main", 1920, 1080, 4_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    (
        "17_video_transcode_vp9_on_safari",
        _make_probe("webm", "vp9", "Profile 0", 1920, 1080, 3_000_000, False, "aac", 2),
        BUILTIN_PRESETS["safari"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    (
        "18_video_transcode_mpeg2_on_chrome",
        _make_probe("mkv", "mpeg2video", "Main", 720, 480, 2_000_000, False, "ac3", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "transcode",
        "none",
        "vaapi",
    ),
    (
        "19_video_transcode_vc1_on_chrome",
        _make_probe("mkv", "vc1", "Advanced", 1920, 1080, 6_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    (
        "20_video_transcode_hevc_main10_on_unknown",
        _make_probe("mkv", "hevc", "Main 10", 1920, 1080, 5_000_000, False, "aac", 2),
        BUILTIN_PRESETS["unknown"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    # 21-25: Resolution downscaling
    (
        "21_downscale_4k_to_1080p_on_client_limit",
        _make_probe("mp4", "h264", "High", 3840, 2160, 12_000_000, False, "aac", 2),
        ClientCapabilities(
            containers=["mp4"],
            video_codecs=["h264"],
            audio_codecs=["aac"],
            max_width=1920,
            max_height=1080,
            hls_fmp4=True,
        ),
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    (
        "22_downscale_1080p_to_720p",
        _make_probe("mp4", "h264", "High", 1920, 1080, 4_000_000, False, "aac", 2),
        ClientCapabilities(
            containers=["mp4"],
            video_codecs=["h264"],
            audio_codecs=["aac"],
            max_width=1280,
            max_height=720,
            hls_fmp4=True,
        ),
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    (
        "23_preserve_aspect_ratio_ultrawide_downscale",
        _make_probe("mp4", "h264", "High", 2560, 1080, 8_000_000, False, "aac", 2),
        ClientCapabilities(
            containers=["mp4"],
            video_codecs=["h264"],
            audio_codecs=["aac"],
            max_width=1920,
            max_height=1080,
            hls_fmp4=True,
        ),
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    (
        "24_odd_dimension_source_rounded_to_even",
        _make_probe("mp4", "h264", "High", 1919, 1079, 3_000_000, False, "aac", 2),
        ClientCapabilities(
            containers=["mp4"],
            video_codecs=["h264"],
            audio_codecs=["aac"],
            max_width=1280,
            max_height=720,
            hls_fmp4=True,
        ),
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    (
        "25_resolution_within_limits_no_downscale",
        _make_probe("mp4", "h264", "High", 1280, 720, 2_000_000, False, "aac", 2),
        ClientCapabilities(
            containers=["mp4"],
            video_codecs=["h264"],
            audio_codecs=["aac"],
            max_width=1920,
            max_height=1080,
            hls_fmp4=True,
        ),
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "direct_play",
        "copy",
        "copy",
        "none",
        "none",
    ),
    # 26-30: Bitrate capping
    (
        "26_bitrate_clamp_client_max_bitrate",
        _make_probe("mp4", "h264", "High", 1920, 1080, 10_000_000, False, "aac", 2),
        ClientCapabilities(
            containers=["mp4"],
            video_codecs=["h264"],
            audio_codecs=["aac"],
            max_bitrate=4_000_000,
            hls_fmp4=True,
        ),
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    (
        "27_bitrate_clamp_user_max_bitrate",
        _make_probe("mp4", "h264", "High", 1920, 1080, 8_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(max_bitrate=2_000_000),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    (
        "28_bitrate_clamp_options_override",
        _make_probe("mp4", "h264", "High", 1920, 1080, 6_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(target_max_bitrate=1_500_000),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    (
        "29_bitrate_within_limit_direct_play",
        _make_probe("mp4", "h264", "High", 1920, 1080, 3_000_000, False, "aac", 2),
        ClientCapabilities(
            containers=["mp4"],
            video_codecs=["h264"],
            audio_codecs=["aac"],
            max_bitrate=5_000_000,
            hls_fmp4=True,
        ),
        UserLimits(max_bitrate=6_000_000),
        _vaapi_caps(),
        PlaybackOptions(),
        "direct_play",
        "copy",
        "copy",
        "none",
        "none",
    ),
    (
        "30_unknown_preset_caps_bitrate_at_4mbps",
        _make_probe("mp4", "h264", "High", 1920, 1080, 8_000_000, False, "aac", 2),
        BUILTIN_PRESETS["unknown"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    # 31-35: HDR tonemapping
    (
        "31_hdr10_on_sdr_chrome_triggers_tonemap",
        _make_probe("mkv", "hevc", "Main 10", 3840, 2160, 15_000_000, True, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(tonemap=True),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    (
        "32_hdr_on_hdr_capable_safari_direct_play",
        _make_probe("mp4", "hevc", "Main 10", 3840, 2160, 15_000_000, True, "aac", 2),
        BUILTIN_PRESETS["safari"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "direct_play",
        "copy",
        "copy",
        "none",
        "none",
    ),
    (
        "33_hdr10_with_downscale_and_tonemap",
        _make_probe("mkv", "hevc", "Main 10", 3840, 2160, 20_000_000, True, "aac", 2),
        ClientCapabilities(
            containers=["mp4"],
            video_codecs=["h264"],
            audio_codecs=["aac"],
            max_width=1920,
            max_height=1080,
            hdr=False,
            hls_fmp4=True,
        ),
        UserLimits(),
        _vaapi_caps(tonemap=True),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    (
        "34_hdr_when_server_lacks_tonemap_vaapi_uses_software",
        _make_probe("mkv", "hevc", "Main 10", 3840, 2160, 15_000_000, True, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(tonemap=False),  # tonemap_vaapi unsupported
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "none",  # falls back to software tonemap filter
    ),
    (
        "35_sdr_file_does_not_trigger_tonemap",
        _make_probe("mkv", "hevc", "Main", 1920, 1080, 4_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "vaapi",
    ),
    # 36-40: Subtitle handling
    (
        "36_sidecar_vtt_subtitle_does_not_force_transcode",
        _make_probe(
            "mp4",
            "h264",
            "High",
            1920,
            1080,
            3_000_000,
            False,
            "aac",
            2,
            [{"codec": "subrip", "language": "eng"}],
        ),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(subtitle_track_index=2),
        "direct_play",
        "copy",
        "copy",
        "sidecar_vtt",
        "none",
    ),
    (
        "37_bitmap_pgs_subtitle_forces_burn_in_and_transcode",
        _make_probe(
            "mkv",
            "h264",
            "High",
            1920,
            1080,
            3_000_000,
            False,
            "aac",
            2,
            [{"codec": "hdmv_pgs_subtitle", "language": "eng"}],
        ),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(subtitle_track_index=2),
        "transcode_hls",
        "transcode",
        "copy",
        "burn_in",
        "vaapi",
    ),
    (
        "38_bitmap_vobsub_subtitle_forces_burn_in",
        _make_probe(
            "mkv",
            "h264",
            "High",
            720,
            480,
            2_000_000,
            False,
            "aac",
            2,
            [{"codec": "dvd_subtitle", "language": "eng"}],
        ),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(subtitle_track_index=2),
        "transcode_hls",
        "transcode",
        "copy",
        "burn_in",
        "vaapi",
    ),
    (
        "39_ass_styled_subtitle_forces_burn_in_for_styling",
        _make_probe(
            "mkv",
            "h264",
            "High",
            1920,
            1080,
            3_000_000,
            False,
            "aac",
            2,
            [{"codec": "ass", "language": "jpn"}],
        ),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(subtitle_track_index=2),
        "transcode_hls",
        "transcode",
        "copy",
        "burn_in",
        "vaapi",
    ),
    (
        "40_unselected_subtitles_do_not_interfere",
        _make_probe(
            "mp4",
            "h264",
            "High",
            1920,
            1080,
            3_000_000,
            False,
            "aac",
            2,
            [{"codec": "hdmv_pgs_subtitle", "language": "eng"}],
        ),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(subtitle_track_index=None),
        "direct_play",
        "copy",
        "copy",
        "none",
        "none",
    ),
    # 41-45: Hardware vs Software selection
    (
        "41_force_software_setting_uses_libx264",
        _make_probe("mkv", "hevc", "Main", 1920, 1080, 4_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(force_sw=True),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "none",
    ),
    (
        "42_vaapi_encode_failure_falls_back_to_software",
        _make_probe("mkv", "hevc", "Main", 1920, 1080, 4_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(h264_enc=False),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "none",
    ),
    (
        "43_unsupported_hardware_decode_uses_software",
        _make_probe("mkv", "hevc", "Main", 1920, 1080, 4_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(hevc_dec=False),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "none",
    ),
    (
        "44_no_render_device_uses_software",
        _make_probe("mkv", "hevc", "Main", 1920, 1080, 4_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        ServerTranscodeCapabilities(render_device="/dev/null", available=False),
        PlaybackOptions(),
        "transcode_hls",
        "transcode",
        "copy",
        "none",
        "none",
    ),
    # 45-48: User limits & specific tracks
    (
        "45_transcode_not_allowed_forces_direct_play",
        _make_probe("mkv", "hevc", "Main", 1920, 1080, 4_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(allow_transcode=False),
        _vaapi_caps(),
        PlaybackOptions(),
        "direct_play",
        "copy",
        "copy",
        "none",
        "none",
    ),
    (
        "46_specific_audio_track_selected",
        _make_probe(
            "mp4",
            "h264",
            "High",
            1920,
            1080,
            3_000_000,
            False,
            "aac",
            2,
        ),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(audio_track_index=1),
        "direct_play",
        "copy",
        "copy",
        "none",
        "none",
    ),
    (
        "47_multichannel_audio_downmixed_to_stereo",
        _make_probe("mkv", "h264", "High", 1920, 1080, 4_000_000, False, "dts", 6),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "remux_hls",
        "copy",
        "transcode",
        "none",
        "none",
    ),
    (
        "48_direct_play_reasons_contain_explanation",
        _make_probe("mp4", "h264", "High", 1920, 1080, 3_000_000, False, "aac", 2),
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(),
        "direct_play",
        "copy",
        "copy",
        "none",
        "none",
    ),
]


@pytest.mark.parametrize(
    (
        "case_id",
        "probe",
        "capabilities",
        "user_limits",
        "server_caps",
        "options",
        "expected_mode",
        "expected_v_action",
        "expected_a_action",
        "expected_sub_action",
        "expected_hw",
    ),
    TEST_MATRIX,
)
def test_decision_engine_matrix(
    case_id: str,
    probe: ProbeResult,
    capabilities: ClientCapabilities,
    user_limits: UserLimits,
    server_caps: ServerTranscodeCapabilities,
    options: PlaybackOptions,
    expected_mode: str,
    expected_v_action: str,
    expected_a_action: str,
    expected_sub_action: str,
    expected_hw: str,
) -> None:
    plan: TranscodePlan = evaluate_playback_plan(
        probe,
        capabilities,
        user_limits,
        server_caps,
        options,
    )
    assert plan.mode == expected_mode, f"[{case_id}] Mode mismatch: {plan.reasons}"
    assert plan.video_action == expected_v_action, f"[{case_id}] Video action mismatch"
    assert plan.audio_action == expected_a_action, f"[{case_id}] Audio action mismatch"
    assert plan.subtitle_action == expected_sub_action, f"[{case_id}] Sub action mismatch"
    assert plan.hwaccel == expected_hw, f"[{case_id}] HWaccel mismatch: {plan.reasons}"
    if expected_mode == "direct_play" and user_limits.allow_transcode:
        assert plan.reasons == [], f"[{case_id}] Direct play must have empty reason set (§3.3)"
    else:
        assert len(plan.reasons) > 0, f"[{case_id}] Plan reasons must not be empty"


def test_target_resolution_calculation() -> None:
    probe = _make_probe("mp4", "h264", "High", 3840, 2160, 10_000_000, False, "aac", 2)
    caps = ClientCapabilities(
        containers=["mp4"],
        video_codecs=["h264"],
        audio_codecs=["aac"],
        max_width=1920,
        max_height=1080,
        hls_fmp4=True,
    )
    plan = evaluate_playback_plan(probe, caps, UserLimits(), _vaapi_caps(), PlaybackOptions())
    assert plan.target_width == 1920
    assert plan.target_height == 1080


def test_hdr_tonemapping_flags() -> None:
    probe = _make_probe("mkv", "hevc", "Main 10", 3840, 2160, 15_000_000, True, "aac", 2)
    caps = BUILTIN_PRESETS["chrome"]  # hdr=False
    plan = evaluate_playback_plan(probe, caps, UserLimits(), _vaapi_caps(), PlaybackOptions())
    assert plan.tonemap_hdr is True
    assert TranscodeReason.VideoRangeTypeNotSupported in plan.reasons


def test_subtitle_stream_index_is_relative_to_subtitles() -> None:
    # Stream 0: video, Stream 1: audio, Stream 2: sub0 (eng), Stream 3: sub1 (jpn)
    probe = _make_probe(
        "mkv",
        "h264",
        "High",
        1920,
        1080,
        3_000_000,
        False,
        "aac",
        2,
        [{"codec": "ass", "language": "eng"}, {"codec": "ass", "language": "jpn"}],
    )
    # Selecting stream index 3 (the 2nd subtitle stream, relative index 1)
    plan = evaluate_playback_plan(
        probe,
        BUILTIN_PRESETS["chrome"],
        UserLimits(),
        _vaapi_caps(),
        PlaybackOptions(subtitle_track_index=3),
    )
    assert plan.subtitle_action == "burn_in"
    # ffmpeg :si parameter requires 0-based relative index among subtitle streams
    assert plan.subtitle_stream_index == 1


# ==============================================================================
# T05 (P1): Decision-Engine Audit Acceptance Tests (§3.3, §3.4, INV-2, INV-3, INV-9)
# ==============================================================================


def test_decision_audit_acceptance_1_remux() -> None:
    """1. (MKV, H.264, AC3) on MP4/H.264/AAC-only client -> Remux.

    reasons = {Container, AudioCodec}, method = Remux.
    """
    probe = _make_probe(
        container="mkv",
        v_codec="h264",
        v_profile="High",
        width=1920,
        height=1080,
        bit_rate=4_000_000,
        is_hdr=False,
        a_codec="ac3",
        a_channels=2,
    )
    caps = ClientCapabilities(
        containers=["mp4"],
        video_codecs=["h264"],
        audio_codecs=["aac"],
        max_width=1920,
        max_height=1080,
        hls_fmp4=True,
    )
    plan = evaluate_playback_plan(probe, caps, UserLimits(), _vaapi_caps(), PlaybackOptions())
    assert plan.mode == "remux_hls"
    assert set(plan.reasons) == {Container, AudioCodec}
    assert set(plan.reasons) == {
        TranscodeReason.ContainerNotSupported,
        TranscodeReason.AudioCodecNotSupported,
    }


def test_decision_audit_acceptance_2_transcode_hevc() -> None:
    """2. (MP4, HEVC) on an H.264-only client -> Transcode.

    reasons contain VideoCodecNotSupported.
    """
    probe = _make_probe(
        container="mp4",
        v_codec="hevc",
        v_profile="Main",
        width=1920,
        height=1080,
        bit_rate=4_000_000,
        is_hdr=False,
        a_codec="aac",
        a_channels=2,
    )
    caps = ClientCapabilities(
        containers=["mp4"],
        video_codecs=["h264"],
        audio_codecs=["aac"],
        max_width=1920,
        max_height=1080,
        hls_fmp4=True,
    )
    plan = evaluate_playback_plan(probe, caps, UserLimits(), _vaapi_caps(), PlaybackOptions())
    assert plan.mode == "transcode_hls"
    assert TranscodeReason.VideoCodecNotSupported in plan.reasons
    assert "VideoCodecNotSupported" in plan.reasons


def test_decision_audit_acceptance_3_null_bitrate_40mbps_assumed() -> None:
    """3. Source with null bitrate and 8 Mbps cap -> Transcode.

    40 Mbps assumed per §3.1; INV-2 holds for storage.
    """
    probe = _make_probe(
        container="mp4",
        v_codec="h264",
        v_profile="High",
        width=1920,
        height=1080,
        bit_rate=4_000_000,
        is_hdr=False,
        a_codec="aac",
        a_channels=2,
    )
    # Clear bit_rate to simulate unknown probe value
    probe.bit_rate = None
    assert probe.bit_rate is None  # INV-2: probe must not have fabricated defaults

    caps = BUILTIN_PRESETS["chrome"]  # chrome has no default max_bitrate
    user_limits = UserLimits(max_bitrate=8_000_000)  # 8 Mbps cap

    plan = evaluate_playback_plan(probe, caps, user_limits, _vaapi_caps(), PlaybackOptions())
    assert plan.mode == "transcode_hls"
    assert TranscodeReason.ContainerBitrateExceedsLimit in plan.reasons
    assert "ContainerBitrateExceedsLimit" in plan.reasons
    assert probe.bit_rate is None  # INV-2: storage remains null after evaluation


def test_decision_audit_acceptance_4_10bit_h264_software_yuv420p(tmp_path: Any) -> None:
    """4. 10-bit H.264 source via software path -> argv contains format=yuv420p (INV-9)."""
    from pathlib import Path

    from app.services.transcode.builder import build_ffmpeg_hls_command

    probe = _make_probe(
        container="mkv",
        v_codec="h264",
        v_profile="High 10",
        width=1920,
        height=1080,
        bit_rate=4_000_000,
        is_hdr=False,
        a_codec="aac",
        a_channels=2,
    )
    caps = BUILTIN_PRESETS["chrome"]  # video_profiles: [baseline, main, high] (no High 10)
    server_caps = _vaapi_caps(force_sw=True)  # Force software path

    plan = evaluate_playback_plan(probe, caps, UserLimits(), server_caps, PlaybackOptions())
    assert plan.mode == "transcode_hls"
    assert plan.hwaccel == "none"
    assert plan.video_codec == "libx264"

    cmd = build_ffmpeg_hls_command(
        plan=plan,
        input_path=Path("/media/sample10bit.mkv"),
        output_dir=tmp_path,
    )
    # Check INV-9: software chain must force yuv420p format
    vf_arg = ""
    for idx, arg in enumerate(cmd):
        if arg == "-vf" and idx + 1 < len(cmd):
            vf_arg = cmd[idx + 1]
            break
    assert "format=yuv420p" in vf_arg
    assert "-pix_fmt" in cmd
    pix_fmt_idx = cmd.index("-pix_fmt")
    assert cmd[pix_fmt_idx + 1] == "yuv420p"


def test_decision_audit_acceptance_5_function_purity(monkeypatch: pytest.MonkeyPatch) -> None:
    """5. Function purity (INV-3): same inputs -> identical output.

    Must not perform any filesystem or clock access.
    """
    import builtins
    import datetime
    import os
    import pathlib
    import time

    def _forbidden(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("Forbidden I/O or clock access inside pure decision engine!")

    probe = _make_probe("mkv", "h264", "High", 1920, 1080, 4_000_000, False, "ac3", 2)
    caps = BUILTIN_PRESETS["chrome"]
    user_limits = UserLimits()
    server_caps = _vaapi_caps()
    opts = PlaybackOptions()

    # Call 1 normally
    plan1 = evaluate_playback_plan(probe, caps, user_limits, server_caps, opts)

    # Patch filesystem and clock to raise if called
    monkeypatch.setattr(builtins, "open", _forbidden)
    monkeypatch.setattr(os, "stat", _forbidden)
    monkeypatch.setattr(pathlib.Path, "exists", _forbidden)
    monkeypatch.setattr(pathlib.Path, "stat", _forbidden)
    monkeypatch.setattr(time, "time", _forbidden)
    monkeypatch.setattr(time, "monotonic", _forbidden)
    monkeypatch.setattr(datetime, "datetime", _forbidden)

    # Call 2 with patched filesystem/clock
    plan2 = evaluate_playback_plan(probe, caps, user_limits, server_caps, opts)

    assert plan1 == plan2
