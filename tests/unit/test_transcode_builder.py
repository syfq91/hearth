"""Snapshot and unit tests for the ffmpeg command builder."""

from pathlib import Path

import pytest

from app.services.transcode.builder import build_ffmpeg_hls_command
from app.services.transcode.decision import TranscodePlan


def test_builder_remux_hls_copy_video_copy_audio(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        audio_stream_index=1,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        start_offset=0.0,
    )
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "copy"
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "hls"
    assert "-hls_segment_type" in cmd and cmd[cmd.index("-hls_segment_type") + 1] == "fmp4"
    assert str(tmp_path / "media.m3u8") == cmd[-1]
    assert "-hwaccel" not in cmd


def test_builder_remux_hls_with_audio_transcode(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="transcode",
        audio_codec="aac",
        audio_channels=2,
        audio_stream_index=1,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        start_offset=0.0,
        audio_bitrate_kbps=192,
    )
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "aac"
    assert "-ac" in cmd and cmd[cmd.index("-ac") + 1] == "2"
    assert "-b:a" in cmd and cmd[cmd.index("-b:a") + 1] == "192k"


def test_builder_full_vaapi(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="vaapi",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        render_device="/dev/dri/renderD128",
    )
    assert "-hwaccel" in cmd and cmd[cmd.index("-hwaccel") + 1] == "vaapi"
    assert (
        "-hwaccel_output_format" in cmd and cmd[cmd.index("-hwaccel_output_format") + 1] == "vaapi"
    )
    assert "-vaapi_device" in cmd and cmd[cmd.index("-vaapi_device") + 1] == "/dev/dri/renderD128"
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "h264_vaapi"
    assert "-vf" in cmd and "scale_vaapi=format=nv12" in cmd[cmd.index("-vf") + 1]


def test_builder_vaapi_with_downscale_and_bitrate(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi",
        audio_action="transcode",
        audio_codec="aac",
        audio_channels=2,
        subtitle_action="none",
        target_width=1920,
        target_height=1080,
        target_bitrate=4_000_000,
        hwaccel="vaapi",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        render_device="/dev/dri/renderD128",
    )
    assert "-vf" in cmd and "scale_vaapi=w=1920:h=1080:format=nv12" in cmd[cmd.index("-vf") + 1]
    assert "-b:v" in cmd and cmd[cmd.index("-b:v") + 1] == "4000k"
    assert "-maxrate" in cmd and cmd[cmd.index("-maxrate") + 1] == "4800k"
    assert "-bufsize" in cmd and cmd[cmd.index("-bufsize") + 1] == "8000k"


def test_builder_vaapi_hdr_tonemap(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="vaapi",
        tonemap_hdr=True,
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/hdr_movie.mkv"),
        tmp_path,
        render_device="/dev/dri/renderD128",
    )
    assert "-vf" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709:extra_hw_frames=32" in vf
    assert "-rc_mode" in cmd and cmd[cmd.index("-rc_mode") + 1] == "VBR"


def test_builder_vaapi_burn_in_subtitles(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="burn_in",
        subtitle_stream_index=2,
        hwaccel="vaapi",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        render_device="/dev/dri/renderD128",
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert "hwdownload" in vf
    assert "format=nv12" in vf
    assert "subtitles=" in vf
    assert "hwupload" in vf
    assert "scale_vaapi" in vf


def test_builder_burn_in_subtitles_uses_container_fonts_dir(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="burn_in",
        subtitle_stream_index=0,
        hwaccel="none",
    )
    fonts_dir = tmp_path / "fonts"
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        fonts_dir=fonts_dir,
    )
    vf = cmd[cmd.index("-vf") + 1]
    sub_symlink = tmp_path / "sub_source.mkv"
    assert f"subtitles='{sub_symlink}':si=0:fontsdir='{fonts_dir}'" in vf

    without_fonts = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
    )
    assert "fontsdir=" not in without_fonts[without_fonts.index("-vf") + 1]


def test_builder_burn_in_subtitles_complex_filename(tmp_path: Path) -> None:
    import os

    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="burn_in",
        subtitle_stream_index=0,
        hwaccel="vaapi",
    )
    complex_file = Path(
        "/media/anime/A Certain Dark Side's Item (2026)"
        "/Season 01/A Certain Dark Item - S01E05 - Infiltration [1080p HDTV].mkv"
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        complex_file,
        tmp_path,
        render_device="/dev/dri/renderD128",
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert "subtitles=" in vf
    sub_symlink = tmp_path / "sub_source.mkv"
    assert sub_symlink.is_symlink()
    assert os.readlink(sub_symlink) == str(complex_file)
    assert f"subtitles='{sub_symlink}':si=0" in vf


@pytest.mark.parametrize(
    "filename",
    [
        "My [Favorite] Movie: 'The Best' (2024).mkv",
        "Show - S01E01 - [1080p] [Dual-Audio] [x265].mkv",
        "Movie with 'single' and \"double\" quotes: test.mp4",
        "Tricky [Name]: Episode 1, Part 2 (Cut's).mkv",
    ],
)
def test_builder_burn_in_subtitles_parametrized_tricky_filenames(
    tmp_path: Path, filename: str
) -> None:
    import os

    session_dir = tmp_path / "session_tricky"
    session_dir.mkdir(parents=True, exist_ok=True)
    media_file = tmp_path / filename

    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="burn_in",
        subtitle_stream_index=1,
        hwaccel="vaapi",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        media_file,
        session_dir,
        render_device="/dev/dri/renderD128",
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert "subtitles=" in vf

    # Symlink must be in session_dir with clean name
    ext = media_file.suffix or ".mkv"
    sub_symlink = session_dir / f"sub_source{ext}"
    assert sub_symlink.is_symlink()
    assert os.readlink(sub_symlink) == str(media_file)
    # The filter must reference the clean symlink, avoiding filtergraph parser crashes
    assert f"subtitles='{sub_symlink}':si=1" in vf
    # Clean symlink path itself must not contain problematic chars
    assert "'" not in sub_symlink.name
    assert ":" not in sub_symlink.name
    assert "[" not in sub_symlink.name
    assert "]" not in sub_symlink.name


def test_builder_software_x264_fallback(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="transcode",
        audio_codec="aac",
        audio_channels=2,
        subtitle_action="none",
        target_width=1280,
        target_height=720,
        target_bitrate=2_000_000,
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
    )
    assert "-hwaccel" not in cmd
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "libx264"
    assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "veryfast"
    assert "-crf" in cmd and cmd[cmd.index("-crf") + 1] == "23"
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert "-vf" in cmd and "scale=1280:720" in cmd[cmd.index("-vf") + 1]


def test_builder_software_tonemap(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
        tonemap_hdr=True,
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/hdr.mkv"),
        tmp_path,
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert "tonemap=" in vf


def test_builder_start_offset_seek_before_input(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="vaapi",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        start_offset=125.5,
    )
    ss_idx = cmd.index("-ss")
    i_idx = cmd.index("-i")
    assert ss_idx < i_idx
    assert cmd[ss_idx + 1] == "125.5"
    assert "-copyts" in cmd
    assert "-avoid_negative_ts" in cmd
    assert cmd[cmd.index("-avoid_negative_ts") + 1] == "disabled"
    assert "-output_ts_offset" not in cmd
    assert cmd[cmd.index("-force_key_frames") + 1] == "expr:gte(t,125.5+n_forced*4)"


def test_builder_start_offset_zero_uses_copyts_and_omits_ss(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        start_offset=0.0,
    )
    assert "-ss" not in cmd
    assert "-copyts" in cmd
    assert "-output_ts_offset" not in cmd


def test_builder_remux_midfile_with_audio_transcode_adds_noaccurate_seek(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="transcode",
        audio_codec="aac",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        start_offset=40.0,
    )
    ss_idx = cmd.index("-ss")
    i_idx = cmd.index("-i")
    assert ss_idx < cmd.index("-noaccurate_seek") < i_idx
    assert "-copyts" in cmd


@pytest.mark.parametrize("start_offset", [0.0, 100.0])
def test_builder_suppresses_fmp4_edit_lists(tmp_path: Path, start_offset: float) -> None:
    """ffmpeg must not encode the resume offset as an fMP4 edit list.

    `-output_ts_offset` otherwise becomes an empty `edts/elst` in init.mp4.
    Firefox's MSE applies that edit list while hls.js also applies its own
    fragment timestampOffset, which buffers a resumed stream a full resume
    offset ahead of the playhead and stalls.
    """
    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        start_offset=start_offset,
    )
    assert cmd.count("-hls_segment_options") == 1
    options = cmd[cmd.index("-hls_segment_options") + 1]
    assert "use_editlist=0" in options.split(":")


def test_builder_hls_flags_temp_file_default(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
    )
    assert cmd.count("-hls_flags") == 1
    idx = cmd.index("-hls_flags")
    flags = cmd[idx + 1].split("+")
    assert "temp_file" in flags


def test_builder_hls_flags_merge_existing_flags(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        hls_flags="independent_segments",
    )
    assert cmd.count("-hls_flags") == 1
    idx = cmd.index("-hls_flags")
    flags = cmd[idx + 1].split("+")
    assert "temp_file" in flags
    assert "independent_segments" in flags
    assert len(flags) == len(set(flags))


def test_builder_hls_flags_no_duplicate_when_already_contains_temp_file(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        hls_flags="temp_file+independent_segments",
    )
    assert cmd.count("-hls_flags") == 1
    idx = cmd.index("-hls_flags")
    flags = cmd[idx + 1].split("+")
    assert flags.count("temp_file") == 1
    assert "independent_segments" in flags


def test_builder_forced_keyframes_libx264(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        segment_duration=4,
    )
    assert "-force_key_frames" in cmd
    assert cmd[cmd.index("-force_key_frames") + 1] == "expr:gte(t,n_forced*4)"
    assert "-sc_threshold" in cmd
    assert cmd[cmd.index("-sc_threshold") + 1] == "0"
    assert "-g" not in cmd
    assert "-keyint_min" not in cmd


def test_builder_forced_keyframes_vaapi(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="vaapi",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        segment_duration=4,
        fps=25.0,
    )
    assert "-force_key_frames" in cmd
    assert cmd[cmd.index("-force_key_frames") + 1] == "expr:gte(t,n_forced*4)"
    assert "-sc_threshold" not in cmd
    assert "-g" in cmd
    assert cmd[cmd.index("-g") + 1] == "100"
    assert "-keyint_min" in cmd
    assert cmd[cmd.index("-keyint_min") + 1] == "100"


def test_builder_forced_keyframes_copy_mode(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        segment_duration=4,
    )
    assert "-force_key_frames" not in cmd
    assert "-sc_threshold" not in cmd
    assert "-g" not in cmd
    assert "-keyint_min" not in cmd


def test_builder_short_first_segment_anchors_keyframes(tmp_path: Path) -> None:
    """A 2s opener keeps 4s segments afterwards by anchoring the keyframe grid."""
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        segment_duration=4,
        first_segment_duration=2.0,
    )
    assert cmd[cmd.index("-force_key_frames") + 1] == "expr:gte(t,2+n_forced*4)"
    assert cmd[cmd.index("-hls_time") + 1] == "2"


def test_builder_short_first_segment_vaapi(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="vaapi",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        segment_duration=4,
        first_segment_duration=2.0,
        fps=24.0,
    )
    assert cmd[cmd.index("-force_key_frames") + 1] == "expr:gte(t,2+n_forced*4)"
    assert cmd[cmd.index("-hls_time") + 1] == "2"
    # The GOP bound still tracks the regular segment duration.
    assert cmd[cmd.index("-g") + 1] == "96"


def test_builder_short_first_segment_ignored_for_copy(tmp_path: Path) -> None:
    """Stream copies cannot force keyframes, so the opening segment stays full length."""
    plan = TranscodePlan(
        mode="remux_hls",
        video_action="copy",
        video_codec="copy",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        segment_duration=4,
        first_segment_duration=2.0,
    )
    assert "-force_key_frames" not in cmd
    assert cmd[cmd.index("-hls_time") + 1] == "4"


def test_builder_short_first_segment_ignored_when_not_shorter(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        tmp_path,
        segment_duration=4,
        first_segment_duration=4.0,
    )
    assert cmd[cmd.index("-force_key_frames") + 1] == "expr:gte(t,n_forced*4)"
    assert cmd[cmd.index("-hls_time") + 1] == "4"


def test_builder_uses_negotiated_software_targets(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx265",
        audio_action="transcode",
        audio_codec="ac3",
        audio_channels=6,
        subtitle_action="none",
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(plan, Path("/media/movie.mkv"), tmp_path)
    assert cmd[cmd.index("-c:v") + 1] == "libx265"
    assert cmd[cmd.index("-c:a") + 1] == "ac3"
    assert "-preset" not in cmd
    assert "-sc_threshold" not in cmd
    assert cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert "-force_key_frames" in cmd


def test_builder_bitmap_burn_in_uses_filter_complex_overlay_software(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="burn_in",
        subtitle_stream_index=0,
        subtitle_is_bitmap=True,
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(plan, Path("/media/movie.mkv"), tmp_path)
    assert "-vf" not in cmd
    assert "-filter_complex" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:v:0][0:s:0]overlay=eof_action=pass" in fc
    assert "[vout]" in fc
    assert cmd[cmd.index("-map") + 1] == "[vout]"


def test_builder_bitmap_burn_in_uses_filter_complex_overlay_vaapi(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="burn_in",
        subtitle_stream_index=1,
        subtitle_is_bitmap=True,
        hwaccel="vaapi",
    )
    cmd = build_ffmpeg_hls_command(
        plan, Path("/media/movie.mkv"), tmp_path, render_device="/dev/dri/renderD128"
    )
    assert "-vf" not in cmd
    assert "-filter_complex" in cmd
    fc = cmd[cmd.index("-filter_complex") + 1]
    assert "hwdownload,format=nv12[v_sw]" in fc
    assert "[v_sw][0:s:1]overlay=eof_action=pass,hwupload,scale_vaapi=format=nv12[vout]" in fc
    assert cmd[cmd.index("-map") + 1] == "[vout]"


def test_builder_sidecar_ass_burn_in_uses_symlink(tmp_path: Path) -> None:
    sidecar = tmp_path / "movie.jpn.ass"
    sidecar.write_text("[Script Info]\nTitle: Test\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="libx264",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="burn_in",
        subtitle_sidecar_id="sidecar-id",
        subtitle_is_bitmap=False,
        hwaccel="none",
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/movie.mkv"),
        out_dir,
        subtitle_sidecar_path=sidecar,
    )
    vf = cmd[cmd.index("-vf") + 1]
    assert "subtitles=" in vf
    assert ":si=" not in vf
    assert (out_dir / "sub_source.ass").is_symlink()


def test_builder_vaapi_hybrid_sw_decode_hw_encode(tmp_path: Path) -> None:
    plan = TranscodePlan(
        mode="transcode_hls",
        video_action="transcode",
        video_codec="h264_vaapi",
        audio_action="copy",
        audio_codec="copy",
        audio_channels=2,
        subtitle_action="none",
        hwaccel="vaapi",
        hwaccel_decode=False,
        target_width=1280,
        target_height=720,
    )
    cmd = build_ffmpeg_hls_command(
        plan,
        Path("/media/av1_movie.mkv"),
        tmp_path,
        render_device="/dev/dri/renderD128",
    )
    assert "-hwaccel" not in cmd
    assert "-vaapi_device" in cmd
    vf = cmd[cmd.index("-vf") + 1]
    assert "format=nv12,hwupload,scale_vaapi=w=1280:h=720:format=nv12" in vf
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "h264_vaapi"
