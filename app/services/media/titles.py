"""Title normalization shared by scanning, sorting and matching."""

import re
import unicodedata

_LEADING_ARTICLES = ("the ", "a ", "an ")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def strip_accents(value: str) -> str:
    """Return an ASCII approximation of a Unicode title."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize_title(value: str) -> str:
    """Lowercase, accent-fold and collapse punctuation for comparisons."""
    folded = strip_accents(value).casefold()
    return _NON_ALNUM.sub(" ", folded).strip()


def sort_title(value: str) -> str:
    """Return a normalized title with a leading English article removed."""
    normalized = normalize_title(value)
    for article in _LEADING_ARTICLES:
        if normalized.startswith(article):
            return normalized[len(article) :].strip()
    return normalized
