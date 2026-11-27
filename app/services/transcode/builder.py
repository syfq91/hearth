"""FFmpeg command builder.

Generates precise argument lists for VAAPI and software HLS transcoding
and remuxing pipelines.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from app.services.transcode.decision import TranscodePlan


def _resolve_subtitle_source_path(input_path: Path, output_dir: Path) -> str:
    """Resolve a safe path for FFmpeg's subtitles filter.

    FFmpeg's filtergraph and libass parsers execute multiple uncoordinated
    escaping passes on filter arguments, making complex paths (containing
    quotes, brackets, colons, or commas) fragile or impossible to parse.
    Creating a clean symlink inside the session output directory bypasses all
    escaping bugs.
    """
    sub_symlink = output_dir / f"sub_source{input_path.suffix or '.mkv'}"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        if sub_symlink.is_symlink() or sub_symlink.exists():
            with contextlib.suppress(OSError):
                sub_symlink.unlink()
        sub_symlink.symlink_to(input_path)
        return str(sub_symlink)
    except OSError:
        return str(input_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _subtitle_burn_in_filter(
    input_path: Path,
    output_dir: Path,
    subtitle_index: int | None,
    fonts_dir: Path | None,
    sidecar_path: Path | None = None,
) -> str:
    """Build the ``subtitles`` filter, with container fonts when available."""
    if sidecar_path is not None:
        sub_path = _resolve_subtitle_source_path(sidecar_path, output_dir)
        value = f"subtitles='{sub_path}'"
    else:
        sub_path = _resolve_subtitle_source_path(input_path, output_dir)
        value = f"subtitles='{sub_path}':si={subtitle_index}"
    if fonts_dir is not None:
        value += f":fontsdir='{fonts_dir}'"
    return value


def _merge_hls_flags(existing_flags: str | None, *new_flags: str) -> str:
    """Merge HLS flag strings using '+' without duplicates."""
    flags: list[str] = []
    if existing_flags:
        for f in existing_flags.split("+"):
            clean = f.strip()
            if clean and clean not in flags:
                flags.append(clean)
    for f in new_flags:
        clean = f.strip()
        if clean and clean not in flags:
            flags.append(clean)
    return "+".join(flags)


def build_ffmpeg_hls_command(
    plan: TranscodePlan,
    input_path: Path,
    output_dir: Path,
    *,
    ffmpeg_binary: str = "ffmpeg",
    render_device: str = "/dev/dri/renderD128",
    start_offset: float = 0.0,
    start_number: int = 0,
    segment_duration: int = 4,
    first_segment_duration: float = 0.0,
    audio_bitrate_kbps: int = 192,
    hls_flags: str | None = None,
    fps: float = 24.0,
    fonts_dir: Path | None = None,
    subtitle_sidecar_path: Path | None = None,
) -> list[str]:
    """Produce argument list for ffmpeg executing HLS fMP4 delivery.

    ``first_segment_duration`` shortens only the opening segment so the client
    can start before a full segment is encoded. It applies to video transcodes
    (which can force keyframes) and is ignored for stream copies, where segment
    boundaries follow the source keyframes.

    ``fonts_dir`` makes libass use fonts extracted from the source container for
    text burn-in instead of whatever fontconfig happens to provide.
    """
    short_first = plan.video_action == "transcode" and 0 < first_segment_duration < segment_duration
    if short_first:
        keyframe_expr = (
            f"expr:gte(t,{start_offset + first_segment_duration:g}+n_forced*{segment_duration})"
            if start_offset > 0.0
            else f"expr:gte(t,{first_segment_duration:g}+n_forced*{segment_duration})"
        )
    elif start_offset > 0.0:
        keyframe_expr = f"expr:gte(t,{start_offset:g}+n_forced*{segment_duration})"
    else:
        keyframe_expr = f"expr:gte(t,n_forced*{segment_duration})"
    hls_time = f"{first_segment_duration:g}" if short_first else str(segment_duration)

    cmd: list[str] = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-stats",
        "-y",
    ]

    # Hardware acceleration input flags
    if plan.hwaccel == "vaapi":
        if plan.hwaccel_decode:
            cmd.extend(
                [
                    "-hwaccel",
                    "vaapi",
                    "-hwaccel_output_format",
                    "vaapi",
                    "-vaapi_device",
                    render_device,
                ]
            )
        else:
            # Hybrid software decode + VAAPI encode: initialize device for hwupload.
            cmd.extend(["-vaapi_device", render_device])

    # Input seek before -i for fast container-level seeking
    if start_offset > 0.0:
        cmd.extend(["-ss", f"{start_offset:g}"])
        if plan.video_action == "copy" and plan.audio_action != "copy":
            cmd.append("-noaccurate_seek")

    cmd.extend(["-i", str(input_path)])

    bitmap_burn_in = (
        plan.video_action != "copy"
        and plan.subtitle_action == "burn_in"
        and plan.subtitle_is_bitmap
        and plan.subtitle_stream_index is not None
    )
    text_burn_in = (
        plan.video_action != "copy"
        and plan.subtitle_action == "burn_in"
        and not plan.subtitle_is_bitmap
        and (plan.subtitle_stream_index is not None or subtitle_sidecar_path is not None)
    )

    # Stream mapping
    cmd.extend(["-map", "[vout]" if bitmap_burn_in else "0:v:0"])
    if plan.audio_stream_index is not None:
        cmd.extend(["-map", f"0:{plan.audio_stream_index}"])
    else:
        cmd.extend(["-map", "0:a:0?"])

    # Video pipeline
    if plan.video_action == "copy":
        cmd.extend(["-c:v", "copy"])
    elif plan.hwaccel == "vaapi":
        if bitmap_burn_in:
            if plan.hwaccel_decode:
                main_chain = (
                    "tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709:extra_hw_frames=32"
                    if plan.tonemap_hdr
                    else "scale_vaapi=format=nv12"
                )
                post_scale = (
                    f",scale_vaapi=w={plan.target_width}:h={plan.target_height}:format=nv12"
                    if plan.target_width and plan.target_height
                    else ""
                )
                filter_complex = (
                    f"[0:v:0]{main_chain}[v_main];"
                    f"[0:s:{plan.subtitle_stream_index}]format=bgra,hwupload=derive_device=vaapi[sub];"
                    f"[v_main][sub]overlay_vaapi=eof_action=pass:repeatlast=0{post_scale}[vout]"
                )
            else:
                post_filters = ["hwupload"]
                if plan.target_width and plan.target_height:
                    post_filters.append(
                        f"scale_vaapi=w={plan.target_width}:h={plan.target_height}:format=nv12"
                    )
                else:
                    post_filters.append("scale_vaapi=format=nv12")
                filter_complex = (
                    f"[0:v:0]format=nv12[v_sw];"
                    f"[v_sw][0:s:{plan.subtitle_stream_index}]overlay=eof_action=pass,"
                    f"{','.join(post_filters)}[vout]"
                )
            cmd.extend(["-filter_complex", filter_complex])
        else:
            filters: list[str] = []
            if text_burn_in:
                subtitle_filter = _subtitle_burn_in_filter(
                    input_path,
                    output_dir,
                    plan.subtitle_stream_index,
                    fonts_dir,
                    sidecar_path=subtitle_sidecar_path,
                )
                if plan.hwaccel_decode:
                    filters.append(f"hwdownload,format=nv12,{subtitle_filter},hwupload")
                else:
                    filters.append(f"format=nv12,{subtitle_filter},hwupload")
                if plan.target_width and plan.target_height:
                    filters.append(
                        f"scale_vaapi=w={plan.target_width}:h={plan.target_height}:format=nv12"
                    )
                else:
                    filters.append("scale_vaapi=format=nv12")
            else:
                if not plan.hwaccel_decode:
                    filters.extend(["format=nv12", "hwupload"])
                if plan.tonemap_hdr:
                    filters.append(
                        "tonemap_vaapi=format=nv12:p=bt709:t=bt709:m=bt709:extra_hw_frames=32"
                    )
                    if plan.target_width and plan.target_height:
                        filters.append(
                            f"scale_vaapi=w={plan.target_width}:h={plan.target_height}:format=nv12"
                        )
                elif plan.target_width and plan.target_height:
                    filters.append(
                        f"scale_vaapi=w={plan.target_width}:h={plan.target_height}:format=nv12"
                    )
                else:
                    filters.append("scale_vaapi=format=nv12")

            cmd.extend(["-vf", ",".join(filters)])
        cmd.extend(["-c:v", plan.video_codec or "h264_vaapi", "-rc_mode", "VBR"])
        if plan.vaapi_low_power:
            cmd.extend(["-low_power", "1"])

        # Forced keyframes for hardware encoders (INV-11)
        gop = max(1, int(round(fps * segment_duration)))
        cmd.extend(
            [
                "-force_key_frames",
                keyframe_expr,
                "-g",
                str(gop),
                "-keyint_min",
                str(gop),
            ]
        )

        if plan.target_bitrate:
            kbps = int(plan.target_bitrate / 1000)
            max_kbps = int(kbps * 1.2)
            buf_kbps = int(kbps * 2.0)
            cmd.extend(
                [
                    "-b:v",
                    f"{kbps}k",
                    "-maxrate",
                    f"{max_kbps}k",
                    "-bufsize",
                    f"{buf_kbps}k",
                ]
            )
        else:
            cmd.extend(["-b:v", "6M", "-maxrate", "8M", "-bufsize", "12M"])
    else:
        # Software fallback
        sw_filters: list[str] = []
        if text_burn_in:
            sw_filters.append(
                _subtitle_burn_in_filter(
                    input_path,
                    output_dir,
                    plan.subtitle_stream_index,
                    fonts_dir,
                    sidecar_path=subtitle_sidecar_path,
                )
            )
        if plan.target_width and plan.target_height:
            sw_filters.append(f"scale={plan.target_width}:{plan.target_height}")
        if plan.tonemap_hdr:
            sw_filters.append("tonemap=tonemap=mobius:param=0.01:desat=0")
        sw_filters.append("format=yuv420p")

        if bitmap_burn_in:
            filter_complex = (
                f"[0:v:0][0:s:{plan.subtitle_stream_index}]overlay=eof_action=pass,"
                f"{','.join(sw_filters)}[vout]"
            )
            cmd.extend(["-filter_complex", filter_complex])
        else:
            cmd.extend(["-vf", ",".join(sw_filters)])
        software_encoder = plan.video_codec or "libx264"
        cmd.extend(["-c:v", software_encoder])
        if software_encoder == "libx264":
            cmd.extend(["-preset", "veryfast", "-crf", "23", "-sc_threshold", "0"])
        # Every software target writes 8-bit 4:2:0 into the fMP4 packager.
        cmd.extend(["-pix_fmt", "yuv420p", "-force_key_frames", keyframe_expr])
        if plan.target_bitrate:
            kbps = int(plan.target_bitrate / 1000)
            max_kbps = int(kbps * 1.2)
            buf_kbps = int(kbps * 2.0)
            cmd.extend(["-maxrate", f"{max_kbps}k", "-bufsize", f"{buf_kbps}k"])

    # Audio pipeline
    if plan.audio_action == "copy":
        cmd.extend(["-c:a", "copy"])
    else:
        cmd.extend(
            [
                "-c:a",
                plan.audio_codec or "aac",
                "-ac",
                str(plan.audio_channels or 2),
                "-b:a",
                f"{audio_bitrate_kbps}k",
            ]
        )

    # Timestamp continuity (matching Jellyfin DynamicHlsController):
    # Preserve container/keyframe timestamps with -copyts so input -ss does not
    # reset PTS to 0 before burn-in subtitle filters run, and remuxed copy
    # streams keep their true keyframe PTS rather than re-stamping an earlier
    # keyframe to start_offset.
    cmd.extend(["-copyts", "-avoid_negative_ts", "disabled"])

    # Output HLS fMP4 configuration.
    #
    # Suppress fMP4 edit lists (edts/elst) so Firefox MSE does not double-apply
    # timestamp offsets alongside hls.js fragment placement.
    cmd.extend(
        [
            "-f",
            "hls",
            "-hls_segment_type",
            "fmp4",
            "-hls_time",
            hls_time,
            "-hls_segment_options",
            "use_editlist=0",
        ]
    )
    if start_number > 0:
        cmd.extend(["-start_number", str(start_number)])

    # Merge HLS flags (temp_file ensures atomic segment writes, INV-5)
    if "-hls_flags" in cmd:
        idx = cmd.index("-hls_flags")
        cmd[idx + 1] = _merge_hls_flags(
            cmd[idx + 1], "temp_file", *(hls_flags.split("+") if hls_flags else [])
        )
    else:
        cmd.extend(["-hls_flags", _merge_hls_flags(hls_flags, "temp_file")])

    cmd.extend(
        [
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "event",
            "-hls_fmp4_init_filename",
            "init.mp4",
            "-hls_segment_filename",
            str(output_dir / "seg_%03d.m4s"),
            str(output_dir / "media.m3u8"),
        ]
    )

    return cmd


__all__ = ["build_ffmpeg_hls_command"]
