"""Tests for TV show filename and path parsing with guessit."""

from pathlib import Path

from app.services.scanner.parsing import ParsedEpisode, parse_episode_path


def test_standard_season_episode_parsing() -> None:
    path = Path("Breaking Bad/Season 01/Breaking Bad - S01E01 - Pilot.mkv")
    parsed = parse_episode_path(path)
    assert isinstance(parsed, ParsedEpisode)
    assert parsed.series_title == "Breaking Bad"
    assert parsed.season_number == 1
    assert parsed.episode_numbers == (1,)
    assert parsed.episode_title == "Pilot"
    assert parsed.is_special is False


def test_scene_release_name_parsing() -> None:
    path = Path("The.Wire.S02E05.720p.HDTV.x264.mkv")
    parsed = parse_episode_path(path)
    assert parsed.series_title == "The Wire"
    assert parsed.season_number == 2
    assert parsed.episode_numbers == (5,)


def test_folder_hint_for_generic_filename_and_year() -> None:
    path = Path("Doctor Who (2005)/Season 1/S01E03.mkv")
    parsed = parse_episode_path(path)
    assert parsed.series_title == "Doctor Who"
    assert parsed.series_year == 2005
    assert parsed.season_number == 1
    assert parsed.episode_numbers == (3,)


def test_compact_season_episode_syntax() -> None:
    path = Path("Fringe/1x04 - The Arrival.mkv")
    parsed = parse_episode_path(path)
    assert parsed.series_title == "Fringe"
    assert parsed.season_number == 1
    assert parsed.episode_numbers == (4,)


def test_multi_episode_files() -> None:
    cases = [
        ("Show/Season 1/Show - S01E01E02.mkv", 1, (1, 2)),
        ("Show/Season 1/Show.S01E01-E02.mkv", 1, (1, 2)),
        ("Show/Season 1/Show - 1x01-02.mkv", 1, (1, 2)),
        ("Show/Season 2/Show.S02E03.S02E04.mkv", 2, (3, 4)),
    ]
    for raw_path, expected_season, expected_episodes in cases:
        parsed = parse_episode_path(raw_path)
        assert parsed.season_number == expected_season, f"Failed season on {raw_path}"
        assert parsed.episode_numbers == expected_episodes, f"Failed episodes on {raw_path}"


def test_daily_shows_date_based() -> None:
    cases = [
        ("The Daily Show/The Daily Show - 2023-10-15 - Guest.mkv", "The Daily Show", "2023-10-15"),
        ("The.Colbert.Report.2014.05.12.HDTV.mkv", "The Colbert Report", "2014-05-12"),
        ("Daily Show/The Daily Show - 2023.10.16.mkv", "The Daily Show", "2023-10-16"),
        ("Daily Show/The Daily Show - 2023_10_17.mkv", "The Daily Show", "2023-10-17"),
    ]
    for raw_path, expected_series, expected_date in cases:
        parsed = parse_episode_path(raw_path)
        assert parsed.series_title == expected_series, f"Failed title on {raw_path}"
        assert parsed.air_date == expected_date, f"Failed date on {raw_path}"


def test_specials_season_zero() -> None:
    cases = [
        ("Show/Specials/Show - S00E01 - The Special.mkv", 0, (1,)),
        ("Show - S00E02.mkv", 0, (2,)),
        ("Show - SP01.mkv", 0, (1,)),
        ("Show - Special 3.mkv", 0, (3,)),
        ("Show/Specials/Episode Name.mkv", 0, ()),
    ]
    for raw_path, expected_season, expected_episodes in cases:
        parsed = parse_episode_path(raw_path)
        assert parsed.is_special is True, f"Failed special flag on {raw_path}"
        assert parsed.season_number == expected_season, f"Failed season on {raw_path}"
        if expected_episodes:
            assert parsed.episode_numbers == expected_episodes, f"Failed episodes on {raw_path}"


def test_anime_absolute_numbering() -> None:
    cases = [
        ("[SubGroup] Attack on Titan - 05 [1080p].mkv", "Attack on Titan", (5,), 5),
        ("[HorribleSubs] Naruto Shippuden - 152 [1080p].mkv", "Naruto Shippuden", (152,), 152),
        ("Death Note - 01 - Rebirth.mkv", "Death Note", (1,), 1),
    ]
    for raw_path, expected_title, expected_episodes, expected_abs in cases:
        parsed = parse_episode_path(raw_path)
        assert parsed.series_title == expected_title, f"Failed title on {raw_path}"
        assert parsed.episode_numbers == expected_episodes, f"Failed episodes on {raw_path}"
        assert parsed.absolute_number == expected_abs, f"Failed absolute number on {raw_path}"


def test_numeric_and_special_character_titles() -> None:
    path = Path("1923/Season 1/1923 - S01E01 - 1923.mkv")
    parsed = parse_episode_path(path)
    assert parsed.series_title == "1923"
    assert parsed.season_number == 1
    assert parsed.episode_numbers == (1,)


def test_root_containment_and_unparseable_graceful_fallback() -> None:
    path = Path("/media/tv/Random Show/Unknown File.mkv")
    parsed = parse_episode_path(path, root="/media/tv")
    assert parsed.series_title == "Random Show"
    assert parsed.season_number is None
    assert parsed.episode_numbers == ()
