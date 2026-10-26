"""Movie filename and folder parsing with ``guessit``.

Movie libraries are usually named as ``Title (Year) Edition.ext`` or with
scene separators. guessit does the heavy lifting; this module adds deterministic
folder hints, numeric-title handling and a safe fallback when guessit cannot
confidently split a name.
"""

import re
from dataclasses import dataclass
from datetime import date as dt_date
from pathlib import Path
from typing import Any

from guessit import guessit

from app.services.media.titles import normalize_title, sort_title

_SEPARATORS = re.compile(r"[._]+")
_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_RELEASE_TOKEN = re.compile(
    r"\b(2160p|1080p|720p|576p|480p|4k|uhd|bluray|blu-ray|bdrip|brrip|webrip|"
    r"web-dl|webdl|hdtv|dvdrip|remux|x264|x265|h264|h265|hevc|av1|aac|ac3|"
    r"dts|truehd|atmos|10bit|hdr10?|dv)\b",
    re.IGNORECASE,
)
_EDITION_FALLBACK = re.compile(
    r"\b(director'?s? cut|extended(?: edition)?|theatrical(?: cut)?|"
    r"unrated|uncut|remastered|special edition|imax|final cut)\b",
    re.IGNORECASE,
)
_PART = re.compile(
    r"\b(?:part|pt)[\s.-]*(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"i{1,3}|iv|v)\b",
    re.IGNORECASE,
)
_PART_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
}
_GENERIC_STEMS = frozenset({"movie", "film", "video", "feature", "main", "title", "index"})
_ROOT_FOLDER_NAMES = _GENERIC_STEMS | frozenset(
    {
        "movies",
        "films",
        "videos",
        "media",
        "library",
        "libraries",
        "downloads",
        "tv",
        "tv shows",
        "tv-shows",
        "tv series",
        "television",
        "series",
        "shows",
    }
)
_SEASON_DIR = re.compile(r"^(?:season|series|s)\s*(\d+)$", re.IGNORECASE)
_SPECIAL_DIR = re.compile(r"^(?:specials?|extras?|season\s*0)$", re.IGNORECASE)
_SPECIAL_FILE = re.compile(r"\b(?:sp|special)[\s.-]*(\d{1,3})\b", re.IGNORECASE)
_DATE_PATTERN = re.compile(r"\b(19\d{2}|20\d{2})[._-](0[1-9]|1[0-2])[._-](0[1-9]|[12]\d|3[01])\b")


@dataclass(frozen=True)
class ParsedEpisode:
    """The episode and series identity extracted from a path."""

    series_title: str
    series_year: int | None = None
    season_number: int | None = None
    episode_numbers: tuple[int, ...] = ()
    episode_title: str | None = None
    air_date: str | None = None
    absolute_number: int | None = None
    is_special: bool = False
    edition: str | None = None

    @property
    def series_sort_title(self) -> str:
        return sort_title(self.series_title)

    @property
    def series_key(self) -> tuple[str, int]:
        return normalize_title(self.series_title), self.series_year or 0

    @property
    def primary_episode_number(self) -> int | None:
        return self.episode_numbers[0] if self.episode_numbers else None


@dataclass(frozen=True)
class ParsedMovie:
    """The movie identity extracted from a path."""

    title: str
    year: int | None = None
    edition: str | None = None
    part: int | None = None

    @property
    def sort_title(self) -> str:
        return sort_title(self.title)

    @property
    def key(self) -> tuple[str, int]:
        return normalize_title(self.title), self.year or 0


def _clean_name(value: str) -> str:
    """Turn dot/underscore separators into spaces without touching hyphens."""
    return " ".join(_SEPARATORS.sub(" ", value).split())


def _first_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _as_int(value: Any) -> int | None:
    value = _first_value(value)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _part_value(raw: str) -> int | None:
    lowered = raw.casefold()
    if lowered.isdigit():
        return int(lowered)
    return _PART_WORDS.get(lowered)


def _extract_part(text: str) -> tuple[int | None, str]:
    """Remove a ``Part N`` marker so it cannot truncate the movie title."""
    match = _PART.search(text)
    if match is None:
        return None, text
    part = _part_value(match.group(1))
    return part, " ".join((text[: match.start()] + " " + text[match.end() :]).split())


def _edition(value: Any) -> str | None:
    value = _first_value(value)
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _numeric_title_fallback(text: str) -> ParsedMovie | None:
    cleaned = _clean_name(text).strip(" -()[]")
    if not cleaned:
        return None
    match = _YEAR.fullmatch(cleaned)
    if match:
        # "1984" and "1917" are titles, not release years.
        return ParsedMovie(title=cleaned, year=None)
    return None


def _fallback(text: str) -> ParsedMovie:
    """Parse a name without guessit, used for numeric titles and errors."""
    cleaned = _clean_name(text)
    numeric = _numeric_title_fallback(cleaned)
    if numeric:
        return numeric

    matches = list(_YEAR.finditer(cleaned))
    year: int | None = None
    title = cleaned
    if matches:
        match = matches[-1]
        candidate = cleaned[: match.start()].strip(" -()[]")
        if candidate:
            title = candidate
            year = int(match.group(1))
    title = " ".join(_RELEASE_TOKEN.sub(" ", title).split()).strip(" -()[]") or cleaned
    edition_match = _EDITION_FALLBACK.search(text)
    return ParsedMovie(
        title=title,
        year=year,
        edition=edition_match.group(1).title() if edition_match else None,
    )


def _parse_name(text: str) -> ParsedMovie:
    prepared = _clean_name(text)
    part, without_part = _extract_part(prepared)

    try:
        data = dict(guessit(without_part, {"type": "movie"}))
    except Exception:  # guessit is best-effort; a scan must still proceed
        data = {}

    title = str(data.get("title") or "").strip(" -()[]")
    alternative = str(data.get("alternative_title") or "").strip(" -()[]")
    if title and alternative:
        title = f"{title} - {alternative}"
    year = _as_int(data.get("year"))
    edition = _edition(data.get("edition"))

    if not title:
        fallback = _fallback(without_part)
        # A bare year such as "1984" parsed by guessit is a title, not a year.
        if _numeric_title_fallback(without_part):
            return ParsedMovie(
                title=fallback.title,
                year=None,
                edition=fallback.edition,
                part=part,
            )
        return ParsedMovie(
            title=fallback.title,
            year=year if year is not None else fallback.year,
            edition=edition or fallback.edition,
            part=part if part is not None else _as_int(data.get("part")),
        )

    if part is None:
        part = _as_int(data.get("part"))

    return ParsedMovie(title=title, year=year, edition=edition, part=part)


def parse_movie_path(path: Path | str, *, root: Path | str | None = None) -> ParsedMovie:
    """Parse a movie path into a title, year, edition and part.

    The folder is used as a hint when the file name has no year, or when the
    file is generically named (``movie.mkv``). A year in the file name always
    takes precedence so one folder can contain unrelated files.
    """
    movie_path = Path(path)
    file_info = _parse_name(movie_path.stem)
    folder_info = _parse_name(movie_path.parent.name)

    generic_file = not file_info.title or normalize_title(file_info.title) in _GENERIC_STEMS
    root_folder = normalize_title(folder_info.title) in _ROOT_FOLDER_NAMES
    use_folder = (
        bool(folder_info.title)
        and not root_folder
        and ((file_info.year is None and folder_info.year is not None) or generic_file)
    )

    chosen = folder_info if use_folder else file_info
    title = chosen.title or file_info.title or movie_path.stem
    year = chosen.year if chosen.year is not None else file_info.year
    edition = chosen.edition or file_info.edition
    part = chosen.part if chosen.part is not None else file_info.part

    return ParsedMovie(title=title, year=year, edition=edition, part=part)


def parse_episode_path(path: Path | str, *, root: Path | str | None = None) -> ParsedEpisode:
    """Parse a TV episode path into series, season, episode numbers, air date and title."""
    file_path = Path(path)
    stem = file_path.stem

    # Detect folder hints (season folder, series folder)
    parent_name = file_path.parent.name
    grandparent_name = file_path.parent.parent.name if file_path.parent.parent else ""

    season_hint: int | None = None
    special_from_folder = False
    series_folder_name = ""

    if _SPECIAL_DIR.match(parent_name.strip()):
        season_hint = 0
        special_from_folder = True
        series_folder_name = grandparent_name
    elif m := _SEASON_DIR.match(parent_name.strip()):
        season_hint = int(m.group(1))
        series_folder_name = grandparent_name
    else:
        series_folder_name = parent_name

    # Check if series folder is root
    if root is not None:
        try:
            rel = file_path.relative_to(Path(root))
            if len(rel.parts) > 1:
                series_folder_name = rel.parts[0]
        except ValueError:
            pass

    series_folder_info = _parse_name(series_folder_name) if series_folder_name else None
    if series_folder_info:
        norm_folder = normalize_title(series_folder_info.title)
        if norm_folder in _ROOT_FOLDER_NAMES:
            series_folder_info = None

    # Check date patterns in stem
    date_match = _DATE_PATTERN.search(stem)
    date_str: str | None = None
    if date_match:
        date_str = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)}"

    # Check special patterns in stem
    special_match = _SPECIAL_FILE.search(stem)
    special_ep: int | None = None
    if special_match:
        special_ep = int(special_match.group(1))

    try:
        data = dict(guessit(stem, {"type": "episode"}))
    except Exception:
        data = {}

    title_from_file = str(data.get("title") or "").strip(" -()[]")
    episode_title = str(data.get("episode_title") or "").strip() or None

    # Series title & year determination
    if series_folder_info and series_folder_info.title:
        if (
            title_from_file
            and sort_title(title_from_file) == sort_title(series_folder_info.title)
            and len(title_from_file) > len(series_folder_info.title)
        ):
            series_title = title_from_file
        else:
            series_title = series_folder_info.title

        series_year = series_folder_info.year or _as_int(data.get("year"))
        if (
            not episode_title
            and title_from_file
            and sort_title(title_from_file) != sort_title(series_title)
            and normalize_title(title_from_file) not in _GENERIC_STEMS
        ):
            episode_title = title_from_file
    elif title_from_file:
        series_title = title_from_file
        series_year = _as_int(data.get("year"))
    else:
        series_title = stem
        series_year = _as_int(data.get("year"))

    # Season number
    season_from_file = _as_int(data.get("season"))
    if season_from_file is not None:
        season_number = season_from_file
    elif season_hint is not None:
        season_number = season_hint
    elif special_match is not None:
        season_number = 0
    else:
        season_number = None

    # Specials flag
    is_special = (
        season_number == 0
        or special_from_folder
        or special_match is not None
        or data.get("episode_details") == "Special"
    )

    # Episode numbers
    raw_ep = data.get("episode")
    episode_numbers: tuple[int, ...]
    if isinstance(raw_ep, (list, tuple)):
        episode_numbers = tuple(
            sorted(int(x) for x in raw_ep if isinstance(x, (int, str)) and str(x).isdigit())
        )
    elif raw_ep is not None:
        episode_numbers = (int(raw_ep),)
    elif special_ep is not None:
        episode_numbers = (special_ep,)
    else:
        episode_numbers = ()

    # Air date
    d = data.get("date")
    air_date: str | None = None
    if isinstance(d, dt_date):
        air_date = d.isoformat()
    elif date_str:
        air_date = date_str

    # When a date is present, guessit sometimes confuses the year with season
    if air_date and date_match and season_number == int(date_match.group(1)):
        season_number = None
        episode_numbers = ()

    # Absolute number
    absolute_number: int | None = None
    if season_from_file is None and season_hint is None and episode_numbers:
        absolute_number = episode_numbers[0]

    if episode_title and date_match and episode_title == date_match.group(3):
        episode_title = None

    edition = _edition(data.get("edition"))

    return ParsedEpisode(
        series_title=series_title,
        series_year=series_year,
        season_number=season_number,
        episode_numbers=episode_numbers,
        episode_title=episode_title,
        air_date=air_date,
        absolute_number=absolute_number,
        is_special=is_special,
        edition=edition,
    )
