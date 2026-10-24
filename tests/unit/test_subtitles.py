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
