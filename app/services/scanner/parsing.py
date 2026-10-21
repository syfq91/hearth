"""Movie filename and folder parsing.

This is intentionally conservative for now. Task 2.4 replaces the internals
with ``guessit`` while keeping this module's public dataclass stable.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from app.services.media.titles import normalize_title, sort_title

_YEAR = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_SEPARATORS = re.compile(r"[._]+")
_EDITION = re.compile(
    r"\b(director'?s? cut|extended(?: edition)?|theatrical(?: cut)?|"
    r"unrated|uncut|remastered|special edition|imax)\b",
    re.IGNORECASE,
)
_PART = re.compile(r"\b(?:part|pt)\s*(\d{1,2})\b", re.IGNORECASE)
_RELEASE_TOKEN = re.compile(
    r"\b(2160p|1080p|720p|576p|480p|4k|uhd|bluray|blu-ray|bdrip|brrip|webrip|"
    r"web-dl|webdl|hdtv|dvdrip|remux|x264|x265|h264|h265|hevc|av1|aac|ac3|"
    r"dts|truehd|atmos|10bit|hdr10?|dv)\b",
    re.IGNORECASE,
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


def _clean(value: str) -> str:
    return " ".join(_SEPARATORS.sub(" ", value).split())


def _split_name(value: str) -> tuple[str, int | None]:
    """Return the part before the last year and the year, if present."""
    cleaned = _clean(value)
    matches = list(_YEAR.finditer(cleaned))
    if not matches:
        return cleaned, None
    match = matches[-1]
    title = cleaned[: match.start()].strip(" -()[]")
    if not title:
        # A numeric title such as "1984" or "1917" is a title, not a year.
        return cleaned, None
    return title, int(match.group(1))


def _strip_release_tokens(title: str) -> str:
    return " ".join(_RELEASE_TOKEN.sub(" ", title).split()).strip(" -")


def parse_movie_path(path: Path | str, *, root: Path | str | None = None) -> ParsedMovie:
    """Parse a movie path into a title, year, edition and part.

    A folder hint wins when the file name has no year of its own, which handles
    layouts such as ``The Matrix (1999)/The.Matrix.mkv``.
    """
    movie_path = Path(path)
    file_title, file_year = _split_name(movie_path.stem)
    folder_title, folder_year = _split_name(movie_path.parent.name)
    year: int | None

    if file_year is None and folder_year is not None and folder_title:
        title = folder_title
        year = folder_year
    else:
        title = file_title or folder_title or movie_path.stem
        year = file_year if file_year is not None else folder_year

    edition_match = _EDITION.search(movie_path.stem)
    part_match = _PART.search(movie_path.stem)
    title = _strip_release_tokens(title) or movie_path.stem

    edition: str | None = None
    if edition_match:
        raw_edition = edition_match.group(1)
        edition = (
            "Director's Cut" if raw_edition.lower().startswith("director") else raw_edition.title()
        )

    return ParsedMovie(
        title=title,
        year=year,
        edition=edition,
        part=int(part_match.group(1)) if part_match else None,
    )
