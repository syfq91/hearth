"""Content rating classification and certification hierarchy.

Maps film and television certifications (MPA and FCC TV Parental Guidelines)
into comparable severity tiers (1..5) so parental controls can filter media items.
"""

from typing import Any

from sqlalchemy import or_
from sqlalchemy.sql import Select

from app.db.models import MediaItem, User

RATING_LEVELS: dict[str, int] = {
    # Tier 1: All ages
    "g": 1,
    "tvy": 1,
    "tv-y": 1,
    "tvg": 1,
    "tv-g": 1,
    # Tier 2: Mild guidance / older children
    "pg": 2,
    "tvy7": 2,
    "tv-y7": 2,
    "tvpg": 2,
    "tv-pg": 2,
    # Tier 3: Teens
    "pg13": 3,
    "pg-13": 3,
    "tv14": 3,
    "tv-14": 3,
    # Tier 4: Restricted / Older teens & adults
    "r": 4,
    # Tier 5: Adults only
    "nc17": 5,
    "nc-17": 5,
    "tvma": 5,
    "tv-ma": 5,
    "ur": 5,
    "unrated": 5,
    "nr": 5,
    "not rated": 5,
    "x": 5,
}


def rating_level(rating: str | None) -> int:
    """Return numeric severity level (1..5) for a content rating string.

    Returns 0 if unrated or unknown.
    """
    if not rating:
        return 0
    clean = rating.lower().strip()
    return RATING_LEVELS.get(clean, 0)


def is_rating_permitted(item_rating: str | None, max_rating: str | None) -> bool:
    """Return whether ``item_rating`` is permitted under ``max_rating``."""
    if not max_rating:
        return True
    limit = rating_level(max_rating)
    if limit == 0:
        return True
    item_lvl = rating_level(item_rating)
    if item_lvl == 0:
        return True
    return item_lvl <= limit


def allowed_content_ratings(max_rating: str | None) -> list[str] | None:
    """Return list of allowed rating strings matching or below ``max_rating``.

    Returns ``None`` if no rating restriction is configured.
    """
    if not max_rating:
        return None
    limit = rating_level(max_rating)
    if limit == 0:
        return None

    allowed: set[str] = set()
    for key, lvl in RATING_LEVELS.items():
        if lvl <= limit:
            allowed.add(key)
            allowed.add(key.upper())
            allowed.add(key.title())
    return list(allowed)


def apply_rating_filter(query: Select[Any], user: User) -> Select[Any]:
    """Constrain query to items permitted by user's max_rating."""
    if user.role == "admin" or not user.max_rating:
        return query
    allowed = allowed_content_ratings(user.max_rating)
    if allowed is None:
        return query
    return query.where(
        or_(
            MediaItem.content_rating.is_(None),
            MediaItem.content_rating.in_(allowed),
        )
    )
