"""Snapshot and unit tests for the ffmpeg command builder."""

from pathlib import Path

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
    assert "-vf" in cmd and "tonemap_vaapi" in cmd[cmd.index("-vf") + 1]


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
