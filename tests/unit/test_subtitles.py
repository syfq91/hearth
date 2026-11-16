"""Unit tests for subtitle ids, discovery and WebVTT conversion."""

from pathlib import Path

import pytest

from app.services.subtitles import (
    convert_to_webvtt,
    decode_subtitle_bytes,
    decode_subtitle_id,
    discover_subtitles,
    encode_subtitle_id,
    subtitle_language,
)


def test_subtitle_ids_round_trip() -> None:
    encoded = encode_subtitle_id("file-1", "Movie.en.srt")
    assert decode_subtitle_id(encoded) is not None
    ref = decode_subtitle_id(encoded)
    assert ref is not None
    assert (ref.file_id, ref.name) == ("file-1", "Movie.en.srt")


def test_ids_are_url_safe() -> None:
    encoded = encode_subtitle_id("file-1", "Amélie (2001).srt")
    assert set(encoded) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


@pytest.mark.parametrize(
    "value",
    [
        "not-base64!!!",
        "aGVsbG8",  # "hello", not JSON
        encode_subtitle_id("file-1", "../escape.srt"),
        encode_subtitle_id("file-1", "/etc/passwd"),
        encode_subtitle_id("file-1", ".hidden.srt"),
        encode_subtitle_id("", "Movie.srt"),
        "eyJmIjogImZpbGUtMSJ9",  # {"f": "file-1"} with no name
    ],
)
def test_invalid_ids_are_rejected(value: str) -> None:
    assert decode_subtitle_id(value) is None


def test_srt_is_converted_with_webvtt_timestamps() -> None:
    source = (
        "1\r\n"
        "00:00:01,500 --> 00:00:03,000\r\n"
        "Hello\r\n"
        "\r\n"
        "2\r\n"
        "00:00:04,000 --> 00:00:05,250\r\n"
        "<i>World</i>\r\n"
    )
    document = convert_to_webvtt(source, ".srt")
    assert document.startswith("WEBVTT\n\n")
    assert "00:00:01.500 --> 00:00:03.000" in document
    assert "00:00:04.000 --> 00:00:05.250" in document
    assert "<i>World</i>" in document
    assert "\r" not in document


def test_srt_fraction_is_padded_to_milliseconds() -> None:
    document = convert_to_webvtt("1\n00:00:01,5 --> 00:00:02,05\nHi\n", ".srt")
    assert "00:00:01.500 --> 00:00:02.050" in document


def test_vtt_passes_through_without_a_second_header() -> None:
    source = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n"
    assert convert_to_webvtt(source, ".vtt") == source


def test_vtt_without_a_header_gets_one() -> None:
    document = convert_to_webvtt("00:00:01.000 --> 00:00:02.000\nHi\n", ".vtt")
    assert document == "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n"


def test_ass_dialogue_is_converted_and_styled_blocks_are_stripped() -> None:
    source = (
        "[Script Info]\n"
        "Title: Example\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:01.50,0:00:03.00,Default,,0,0,0,,"
        "{\\i1}Hello{\\i0}, world\\Nsecond line\n"
        "Comment: 0,0:00:03.00,0:00:04.00,Default,,0,0,0,,ignored\n"
    )
    document = convert_to_webvtt(source, ".ass")
    assert document.startswith("WEBVTT\n\n")
    assert "00:00:01.500 --> 00:00:03.000" in document
    assert "Hello, world\nsecond line" in document
    assert "ignored" not in document
    assert "{\\i1}" not in document


def test_ass_uses_the_declared_field_order() -> None:
    source = (
        "[Events]\nFormat: Start, End, Text\nDialogue: 0:00:02.00,0:00:04.00,Text, with a comma\n"
    )
    document = convert_to_webvtt(source, ".ass")
    assert "00:00:02.000 --> 00:00:04.000\nText, with a comma" in document


def test_ass_without_a_format_line_uses_ssa_defaults() -> None:
    source = (
        "[Events]\nDialogue: Marked=0,0:00:02.00,0:00:03.00,Default,NTP,0000,0000,0000,,Fallback\n"
    )
    document = convert_to_webvtt(source, ".ssa")
    assert "00:00:02.000 --> 00:00:03.000\nFallback" in document


@pytest.mark.parametrize(
    "dialogue",
    [
        "Dialogue: 0,0:00:03.00,0:00:02.00,Default,,0,0,0,,Backwards",
        "Dialogue: 0,not-a-time,0:00:02.00,Default,,0,0,0,,Broken",
        "Dialogue: 0,0:00:01.00,0:00:02.00,Default,,0,0,0,,",
    ],
)
def test_invalid_ass_cues_are_skipped(dialogue: str) -> None:
    source = (
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"{dialogue}\n"
    )
    assert convert_to_webvtt(source, ".ass") == "WEBVTT\n\n"


def test_ass_text_is_html_escaped() -> None:
    source = "[Events]\nFormat: Start, End, Text\nDialogue: 0:00:01.00,0:00:02.00,5 < 6 & 7 > 4\n"
    document = convert_to_webvtt(source, ".ass")
    assert "5 &lt; 6 &amp; 7 &gt; 4" in document


def test_webvtt_shift_moves_cue_boundaries() -> None:
    source = "WEBVTT\n\n00:00:01.000 --> 00:00:02.500\nHello\n"
    shifted = convert_to_webvtt(source, ".vtt", shift_ms=1500)
    assert shifted == "WEBVTT\n\n00:00:02.500 --> 00:00:04.000\nHello\n"


def test_webvtt_shift_is_applied_to_srt_and_ass() -> None:
    srt = "1\n00:00:05,000 --> 00:00:06,000\nHi\n"
    assert "00:00:04.000 --> 00:00:05.000" in convert_to_webvtt(srt, ".srt", shift_ms=-1000)

    ass = "[Events]\nFormat: Start, End, Text\nDialogue: 0:00:05.00,0:00:06.00,Hi\n"
    assert "00:00:04.000 --> 00:00:05.000" in convert_to_webvtt(ass, ".ass", shift_ms=-1000)


def test_webvtt_shift_clamps_at_zero_and_leaves_metadata_alone() -> None:
    source = (
        "WEBVTT\n"
        "X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000\n\n"
        "00:00:00.500 --> 00:00:01.500\nHi\n"
    )
    shifted = convert_to_webvtt(source, ".vtt", shift_ms=-2000)
    assert "X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:900000" in shifted
    assert "00:00:00.000 --> 00:00:00.000" in shifted


def test_webvtt_shift_zero_is_a_no_op() -> None:
    source = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHi\n"
    assert convert_to_webvtt(source, ".vtt", shift_ms=0) == source


def test_unsupported_formats_are_rejected() -> None:
    with pytest.raises(ValueError):
        convert_to_webvtt("text", ".sub")


def test_subtitle_bytes_tolerate_bom_and_windows_encoding() -> None:
    assert decode_subtitle_bytes(b"\xef\xbb\xbfHello") == "Hello"
    assert decode_subtitle_bytes(b"Am\xe9lie") == "Amélie"


def test_discovery_only_returns_supported_sidecars(tmp_path: Path) -> None:
    video = tmp_path / "Movie (2000).mkv"
    video.write_bytes(b"video")
    for name in ("Movie (2000).en.srt", "Movie (2000).ass", "Movie (2000).sub"):
        (tmp_path / name).write_bytes(b"subtitle")
    (tmp_path / "Unrelated.srt").write_bytes(b"other")

    found = sorted(path.name for path in discover_subtitles(video))
    assert found == ["Movie (2000).ass", "Movie (2000).en.srt"]


def test_language_is_taken_from_the_sidecar_name(tmp_path: Path) -> None:
    video = tmp_path / "Movie.mkv"
    assert subtitle_language(video, tmp_path / "Movie.en.srt") == "en"
    assert subtitle_language(video, tmp_path / "Movie.EN.forced.srt") == "en"
    assert subtitle_language(video, tmp_path / "Movie.srt") is None
    assert subtitle_language(video, tmp_path / "Other.en.srt") is None


@pytest.mark.asyncio
async def test_extract_embedded_subtitle_handles_missing_file(tmp_path: Path) -> None:
    from app.services.subtitles import extract_embedded_subtitle

    result = await extract_embedded_subtitle(tmp_path / "nonexistent.mkv", 0, fmt="webvtt")
    assert result == "WEBVTT\n\n"
    vtt_result = await extract_embedded_subtitle(tmp_path / "nonexistent.mkv", 0, fmt="vtt")
    assert vtt_result == "WEBVTT\n\n"


def test_embedded_subtitle_cache_is_keyed_by_file_revision(tmp_path: Path) -> None:
    from app.services.subtitles import embedded_subtitle_cache_dir

    first = embedded_subtitle_cache_dir(tmp_path, "file-1", 1000.0, 42)
    assert embedded_subtitle_cache_dir(tmp_path, "file-1", 1000.0, 42) == first
    assert embedded_subtitle_cache_dir(tmp_path, "file-1", 1001.0, 42) != first
    assert embedded_subtitle_cache_dir(tmp_path, "file-1", 1000.0, 43) != first
    assert embedded_subtitle_cache_dir(tmp_path, "file-2", 1000.0, 42) != first


def test_embedded_subtitle_command_extracts_all_tracks_in_one_pass(tmp_path: Path) -> None:
    from app.services.subtitles import build_embedded_subtitle_command

    cmd = build_embedded_subtitle_command(
        Path("/media/movie.mkv"),
        [(0, "ass"), (0, "vtt"), (2, "vtt")],
        tmp_path,
    )
    assert cmd.count("-map") == 3
    assert cmd.count("-i") == 1
    assert str(tmp_path / "0.ass") in cmd
    assert str(tmp_path / "0.vtt") in cmd
    assert str(tmp_path / "2.vtt") in cmd
    ass_output = cmd.index(str(tmp_path / "0.ass"))
    assert cmd[ass_output - 6 : ass_output] == [
        "-map",
        "0:s:0",
        "-c:s",
        "copy",
        "-f",
        "ass",
    ]


@pytest.mark.asyncio
async def test_ensure_embedded_subtitles_caches_every_output_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import subtitles

    calls: list[list[str]] = []

    async def fake_run(cmd: list[str], *, timeout: float) -> None:
        calls.append(cmd)
        for index, value in enumerate(cmd[:-1]):
            if value == "-f" and index + 2 < len(cmd):
                Path(cmd[index + 2]).write_text("cached track", encoding="utf-8")

    monkeypatch.setattr(subtitles, "_run_subtitle_ffmpeg", fake_run)
    cache_dir = tmp_path / "cache"
    tracks = [
        subtitles.EmbeddedSubtitleTrack(ordinal=0, formats=("ass", "vtt")),
        subtitles.EmbeddedSubtitleTrack(ordinal=2, formats=("vtt",)),
    ]

    await subtitles.ensure_embedded_subtitles(
        tmp_path / "movie.mkv", tracks, cache_dir, timeout=1.0
    )
    assert len(calls) == 1
    assert (cache_dir / "0.ass").read_text() == "cached track"
    assert (cache_dir / "0.vtt").read_text() == "cached track"
    assert (cache_dir / "2.vtt").read_text() == "cached track"

    await subtitles.ensure_embedded_subtitles(
        tmp_path / "movie.mkv", tracks, cache_dir, timeout=1.0
    )
    assert len(calls) == 1
