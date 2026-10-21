"""Edge-case tests for movie filename and folder parsing."""

from pathlib import Path

import pytest

from app.services.scanner.parsing import ParsedMovie, parse_movie_path


def _parse(name: str, *, folder: str | None = None) -> ParsedMovie:
    path = Path(folder or "/media/movies") / name
    return parse_movie_path(path)


@pytest.mark.parametrize(
    ("name", "title", "year"),
    [
        ("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv", "The Matrix", 1999),
        ("Amélie (2001).mkv", "Amélie", 2001),
        ("10.Cloverfield.Lane.2016.mkv", "10 Cloverfield Lane", 2016),
        ("Spider-Man.No.Way.Home.2021.EXTENDED.1080p.WEB-DL.mkv", "Spider-Man No Way Home", 2021),
        ("The.Wolf.of.Wall.Street.2013.mkv", "The Wolf of Wall Street", 2013),
        (
            "Dr. Strangelove or How I Learned to Stop Worrying and Love the Bomb (1964).mkv",
            "Dr Strangelove or How I Learned to Stop Worrying and Love the Bomb",
            1964,
        ),
        ("Movie_Without_Year.mkv", "Movie Without Year", None),
        ("Film [2160p] [HDR].mkv", "Film", None),
    ],
)
def test_title_and_year(name: str, title: str, year: int | None) -> None:
    parsed = _parse(name)
    assert parsed.title == title
    assert parsed.year == year


@pytest.mark.parametrize(
    ("name", "title", "year"),
    [
        ("2001_A_Space_Odyssey_1968.mkv", "2001 A Space Odyssey", 1968),
        ("1917.2019.2160p.UHD.BluRay.REMUX.mkv", "1917", 2019),
    ],
)
def test_years_inside_titles(name: str, title: str, year: int) -> None:
    parsed = _parse(name)
    assert parsed.title == title
    assert parsed.year == year


@pytest.mark.parametrize("name", ["1984.mkv", "1917.mkv", "2012.mkv"])
def test_year_only_names_are_titles(name: str) -> None:
    parsed = _parse(name)
    assert parsed.title == name.removesuffix(".mkv")
    assert parsed.year is None


def test_editions_and_parts() -> None:
    director = _parse("Blade Runner 2049 (2017) Director's Cut.mkv")
    assert director.title == "Blade Runner 2049"
    assert director.edition == "Director's Cut"

    extended = _parse("The Lord of the Rings (2001) Extended Edition.mkv")
    assert extended.title == "The Lord of the Rings"
    assert extended.edition == "Extended"

    part = _parse("Dune Part Two (2024).mkv")
    assert (part.title, part.year, part.part) == ("Dune", 2024, 2)

    worded = _parse("Mission Impossible - Dead Reckoning Part One (2023).mkv")
    assert worded.title == "Mission Impossible - Dead Reckoning"
    assert worded.part == 1

    numbered = _parse("Blade Runner 2049 (2017) Director's Cut Part 2.mkv")
    assert (numbered.title, numbered.edition, numbered.part) == (
        "Blade Runner 2049",
        "Director's Cut",
        2,
    )


def test_folder_hint_when_file_has_no_year() -> None:
    parsed = _parse("The.Matrix.1080p.BluRay.x264.mkv", folder="/media/movies/The Matrix (1999)")
    assert (parsed.title, parsed.year) == ("The Matrix", 1999)


def test_generic_file_uses_movie_folder() -> None:
    parsed = _parse("movie.mkv", folder="/media/movies/The Matrix (1999)")
    assert (parsed.title, parsed.year) == ("The Matrix", 1999)

    parsed = _parse("video.mkv", folder="/media/movies/The Matrix")
    assert parsed.title == "The Matrix"


def test_root_folder_name_is_not_used_as_title() -> None:
    parsed = _parse("Arrival.2016.mkv", folder="/media/movies")
    assert (parsed.title, parsed.year) == ("Arrival", 2016)

    parsed = _parse("Arrival (2016).mkv", folder="/media/films")
    assert parsed.title == "Arrival"


def test_file_year_wins_over_folder_year() -> None:
    parsed = _parse("Arrival (2016).mkv", folder="/media/movies/Wrong Folder (1999)")
    assert (parsed.title, parsed.year) == ("Arrival", 2016)


def test_versions_of_one_movie_share_a_key() -> None:
    first = _parse("The Matrix (1999) 1080p.mkv")
    second = _parse("The Matrix (1999) 2160p.mkv", folder="/media/movies/The Matrix (1999)")
    assert first.key == second.key == ("the matrix", 1999)


def test_unicode_and_punctuation() -> None:
    parsed = _parse("Léon - The Professional (1994).mkv")
    assert parsed.title == "Léon - The Professional"
    assert parsed.year == 1994
    assert parsed.sort_title == "leon the professional"


def test_guessit_failure_falls_back_without_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.scanner.parsing as parsing

    def explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("guessit exploded")

    monkeypatch.setattr(parsing, "guessit", explode)
    parsed = _parse("Fallback Movie (2020).mkv")
    assert parsed.title == "Fallback Movie"
    assert parsed.year == 2020
