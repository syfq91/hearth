"""Movie filename and folder parsing with ``guessit``.

Movie libraries are usually named as ``Title (Year) Edition.ext`` or with
scene separators. guessit does the heavy lifting; this module adds deterministic
folder hints, numeric-title handling and a safe fallback when guessit cannot
confidently split a name.
"""

import re
from dataclasses import dataclass
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
    {"movies", "films", "videos", "media", "library", "libraries", "downloads"}
)


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
