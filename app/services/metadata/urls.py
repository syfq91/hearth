"""Parse public metadata-provider URLs into matchable identifiers.

Administrators can paste a link to the exact item (a TMDB episode, a Google
Books volume, or an Open Library work) into the manual-match search box. These
helpers turn such a link into the provider name, the plain provider id, and the
media type so the matcher can fetch that item's metadata directly.

Only the canonical public web URLs are recognised; API endpoints and arbitrary
hosts are rejected. Returning ``None`` means "not a supported provider URL".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

_TMDB_HOSTS = frozenset({"themoviedb.org", "www.themoviedb.org"})
_GOOGLE_BOOKS_HOSTS = frozenset({"books.google.com", "www.google.com", "play.google.com"})
_OPENLIBRARY_HOSTS = frozenset({"openlibrary.org", "www.openlibrary.org"})

# TMDB embeds the numeric id before an optional slug, e.g. /movie/603-the-matrix.
_LEADING_INT = re.compile(r"^(\d+)")
# Google Books volume ids are opaque, e.g. "B1R1CwAAQBAJ".
_VOLUME_ID = re.compile(r"^[A-Za-z0-9_-]{8,}$")


class UrlResolutionError(ValueError):
    """A pasted URL cannot be used to match the selected item."""


@dataclass(frozen=True)
class ProviderUrl:
    """A public provider URL resolved to the fields needed for a direct match."""

    provider: str
    provider_id: str
    media_type: str
    season_number: int | None = None
    episode_number: int | None = None


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def _leading_int(value: str) -> int | None:
    match = _LEADING_INT.match(value)
    return int(match.group(1)) if match else None


def _parse_tmdb(parts: list[str]) -> ProviderUrl | None:
    if len(parts) < 2:
        return None
    kind = parts[0].casefold()
    if kind not in {"movie", "tv"}:
        return None

    numeric_id = _leading_int(parts[1])
    if numeric_id is None:
        return None
    provider_id = str(numeric_id)

    if kind == "movie":
        return ProviderUrl(provider="tmdb", provider_id=provider_id, media_type="movie")
    if len(parts) == 2:
        return ProviderUrl(provider="tmdb", provider_id=provider_id, media_type="series")

    if len(parts) < 4 or parts[2].casefold() != "season":
        return None
    season_number = _leading_int(parts[3])
    if season_number is None:
        return None

    if len(parts) >= 6 and parts[4].casefold() == "episode":
        episode_number = _leading_int(parts[5])
        if episode_number is None:
            return None
        return ProviderUrl(
            provider="tmdb",
            provider_id=provider_id,
            media_type="episode",
            season_number=season_number,
            episode_number=episode_number,
        )
    return ProviderUrl(
        provider="tmdb",
        provider_id=provider_id,
        media_type="season",
        season_number=season_number,
    )


def _parse_google_books(parts: list[str], query: str) -> ProviderUrl | None:
    params = parse_qs(query)
    for key in ("id", "vid"):
        values = params.get(key) or []
        for value in values:
            volume_id = value.strip()
            if _VOLUME_ID.fullmatch(volume_id):
                return ProviderUrl(provider="googlebooks", provider_id=volume_id, media_type="book")

    # /books/edition/<Title>/<VolumeId> (the title slug is optional).
    if "books" in parts:
        tail = parts[parts.index("books") + 1 :]
        if len(tail) >= 2 and tail[0].casefold() == "edition":
            volume_id = tail[-1]
            if _VOLUME_ID.fullmatch(volume_id):
                return ProviderUrl(provider="googlebooks", provider_id=volume_id, media_type="book")
    return None


def _parse_openlibrary(parts: list[str]) -> ProviderUrl | None:
    if len(parts) < 2:
        return None
    kind = parts[0].casefold()
    key = parts[1]
    if kind not in {"works", "books"} or not key:
        return None
    return ProviderUrl(provider="openlibrary", provider_id=f"/{kind}/{key}", media_type="book")


def parse_provider_url(value: str) -> ProviderUrl | None:
    """Return the provider and id encoded in a public URL, or ``None``.

    Accepts TMDB movie/series/season/episode pages, Google Books volumes and
    Open Library work/edition pages. Both ``http`` and ``https`` are accepted;
    hostnames are compared case-insensitively and ``www.`` is tolerated.
    """

    candidate = value.strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None

    host = parsed.hostname.casefold()
    parts = _segments(parsed.path)
    if host in _TMDB_HOSTS:
        return _parse_tmdb(parts)
    if host in _GOOGLE_BOOKS_HOSTS:
        return _parse_google_books(parts, parsed.query)
    if host in _OPENLIBRARY_HOSTS:
        return _parse_openlibrary(parts)
    return None
