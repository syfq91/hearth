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
    audio_bitrate_kbps: int = 192,
    hls_flags: str | None = None,
    fps: float = 24.0,
) -> list[str]:
    """Produce argument list for ffmpeg executing HLS fMP4 delivery."""
    cmd: list[str] = [
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
    ]

    # Hardware acceleration input flags
    if plan.hwaccel == "vaapi":
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

    # Input seek before -i for fast container-level seeking
    if start_offset > 0.0:
        cmd.extend(["-ss", f"{start_offset:g}"])

    cmd.extend(["-i", str(input_path)])

    # Stream mapping
    cmd.extend(["-map", "0:v:0"])
    if plan.audio_stream_index is not None:
        cmd.extend(["-map", f"0:{plan.audio_stream_index}"])
    else:
        cmd.extend(["-map", "0:a:0?"])

    # Video pipeline
    if plan.video_action == "copy":
        cmd.extend(["-c:v", "copy"])
    elif plan.hwaccel == "vaapi":
        filters: list[str] = []
        if plan.subtitle_action == "burn_in" and plan.subtitle_stream_index is not None:
            sub_path = _resolve_subtitle_source_path(input_path, output_dir)
            filters.append(
                f"hwdownload,format=nv12,subtitles='{sub_path}':si={plan.subtitle_stream_index},hwupload"
            )
            if plan.target_width and plan.target_height:
                filters.append(
                    f"scale_vaapi=w={plan.target_width}:h={plan.target_height}:format=nv12"
                )
            else:
                filters.append("scale_vaapi=format=nv12")
        else:
            if plan.target_width and plan.target_height:
                filters.append(
                    f"scale_vaapi=w={plan.target_width}:h={plan.target_height}:format=nv12"
                )
            else:
                filters.append("scale_vaapi=format=nv12")
            if plan.tonemap_hdr:
                filters.append("tonemap_vaapi")

        cmd.extend(["-vf", ",".join(filters)])
        cmd.extend(["-c:v", plan.video_codec or "h264_vaapi"])

        # Forced keyframes for hardware encoders (INV-11)
        gop = max(1, int(round(fps * segment_duration)))
        cmd.extend(
            [
                "-force_key_frames",
                f"expr:gte(t,n_forced*{segment_duration})",
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
        if plan.subtitle_action == "burn_in" and plan.subtitle_stream_index is not None:
            sub_path = _resolve_subtitle_source_path(input_path, output_dir)
            sw_filters.append(f"subtitles='{sub_path}':si={plan.subtitle_stream_index}")
        if plan.target_width and plan.target_height:
            sw_filters.append(f"scale={plan.target_width}:{plan.target_height}")
        if plan.tonemap_hdr:
            sw_filters.append("tonemap=tonemap=mobius:param=0.01:desat=0")
        sw_filters.append("format=yuv420p")

        cmd.extend(["-vf", ",".join(sw_filters)])
        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-force_key_frames",
                f"expr:gte(t,n_forced*{segment_duration})",
                "-sc_threshold",
                "0",
            ]
        )
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
                "aac",
                "-ac",
                str(plan.audio_channels or 2),
                "-b:a",
                f"{audio_bitrate_kbps}k",
            ]
        )

    # Output HLS fMP4 configuration
    cmd.extend(
        [
            "-f",
            "hls",
            "-hls_segment_type",
            "fmp4",
            "-hls_time",
            str(segment_duration),
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
