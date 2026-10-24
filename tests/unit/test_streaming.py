"""Unit tests for HTTP byte-range parsing."""

import pytest

from app.services.streaming import ByteRange, RangeNotSatisfiable, parse_range_header


def test_absent_or_empty_header_means_whole_file() -> None:
    assert parse_range_header(None, 1000) is None
    assert parse_range_header("", 1000) is None


def test_explicit_closed_range() -> None:
    assert parse_range_header("bytes=0-99", 1000) == ByteRange(0, 99)
    assert parse_range_header("bytes=0-0", 1000) == ByteRange(0, 0)


def test_open_ended_range_runs_to_eof() -> None:
    assert parse_range_header("bytes=100-", 1000) == ByteRange(100, 999)
    assert parse_range_header("bytes=0-", 1000) == ByteRange(0, 999)


def test_suffix_range_reads_from_the_end() -> None:
    assert parse_range_header("bytes=-50", 1000) == ByteRange(950, 999)
    assert parse_range_header("bytes=-1", 1000) == ByteRange(999, 999)


def test_suffix_longer_than_file_is_clamped_to_the_whole_file() -> None:
    assert parse_range_header("bytes=-5000", 1000) == ByteRange(0, 999)


def test_end_past_eof_is_clamped() -> None:
    assert parse_range_header("bytes=990-5000", 1000) == ByteRange(990, 999)


def test_whitespace_around_the_range_is_ignored() -> None:
    assert parse_range_header("bytes= 0-10 ", 1000) == ByteRange(0, 10)


@pytest.mark.parametrize(
    "header",
    [
        "bytes=1000-",
        "bytes=1000-1010",
        "bytes=-0",
    ],
)
def test_unsatisfiable_ranges_raise(header: str) -> None:
    with pytest.raises(RangeNotSatisfiable):
        parse_range_header(header, 1000)


@pytest.mark.parametrize(
    "header",
    [
        "bytes=10-5",
        "items=0-10",
        "bytes=abc-def",
        "bytes=",
        "bytes=0-1,5-6",
        "bytes=1-2-3",
    ],
)
def test_invalid_or_unsupported_headers_are_ignored(header: str) -> None:
    assert parse_range_header(header, 1000) is None


def test_any_explicit_range_on_an_empty_file_is_unsatisfiable() -> None:
    assert parse_range_header(None, 0) is None
    with pytest.raises(RangeNotSatisfiable):
        parse_range_header("bytes=0-", 0)
