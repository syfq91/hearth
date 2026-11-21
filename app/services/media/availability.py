"""Visibility predicates for catalog items backed by on-disk media files.

The scanner soft-deletes a file that vanished from disk by setting
``MediaFile.missing_since`` while keeping the catalog rows for a grace period.
User-facing queries must hide an item once none of its files are available.

Container items (series, seasons, albums, artists) do not own files directly;
their availability is derived from their episodes/tracks. Episode and track
files can be linked either by ``MediaFile.item_id`` or by the type-specific
``file_id`` (multi-file/multi-episode case), so both are considered.

An item with no file rows at all is treated as visible: that state is either a
transient step in reconciliation or a catalog row created without a file, and
hiding it would change existing behavior beyond soft-delete handling.
"""

from collections.abc import Callable
from functools import lru_cache
from typing import Any

from sqlalchemy import ColumnElement, case, not_, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql import Select

from app.db.models import Episode, MediaFile, MediaItem, Track


def _episode_has_file(
    criterion: Callable[[Any], ColumnElement[bool]],
    active_only: bool,
) -> ColumnElement[bool]:
    """An episode matching ``criterion`` that is backed by a file."""
    episode = aliased(Episode, name="availability_episode")
    direct_file = aliased(MediaFile, name="availability_episode_direct_file")
    linked_file = aliased(MediaFile, name="availability_episode_linked_file")

    direct: Select[Any] = (
        select(1)
        .select_from(episode)
        .join(direct_file, direct_file.item_id == episode.item_id)
        .where(criterion(episode))
    )
    linked: Select[Any] = (
        select(1)
        .select_from(episode)
        .join(linked_file, linked_file.id == episode.file_id)
        .where(criterion(episode))
    )
    if active_only:
        direct = direct.where(direct_file.missing_since.is_(None))
        linked = linked.where(linked_file.missing_since.is_(None))
    return or_(direct.exists(), linked.exists())


def _track_has_file(
    criterion: Callable[[Any], ColumnElement[bool]],
    active_only: bool,
) -> ColumnElement[bool]:
    """A track matching ``criterion`` that is backed by a file."""
    track = aliased(Track, name="availability_track")
    direct_file = aliased(MediaFile, name="availability_track_direct_file")
    linked_file = aliased(MediaFile, name="availability_track_linked_file")

    direct: Select[Any] = (
        select(1)
        .select_from(track)
        .join(direct_file, direct_file.item_id == track.item_id)
        .where(criterion(track))
    )
    linked: Select[Any] = (
        select(1)
        .select_from(track)
        .join(linked_file, linked_file.id == track.file_id)
        .where(criterion(track))
    )
    if active_only:
        direct = direct.where(direct_file.missing_since.is_(None))
        linked = linked.where(linked_file.missing_since.is_(None))
    return or_(direct.exists(), linked.exists())


def _file_predicate(active_only: bool) -> ColumnElement[bool]:
    """Cornerstone predicate: at least one file, optionally restricted to active ones."""
    active = MediaFile.missing_since.is_(None)
    direct = MediaItem.files.any(active) if active_only else MediaItem.files.any()
    return case(
        (
            MediaItem.type == "episode",
            or_(direct, _episode_has_file(lambda ep: ep.item_id == MediaItem.id, active_only)),
        ),
        (
            MediaItem.type == "series",
            _episode_has_file(lambda ep: ep.series_id == MediaItem.id, active_only),
        ),
        (
            MediaItem.type == "season",
            _episode_has_file(lambda ep: ep.season_id == MediaItem.id, active_only),
        ),
        (
            MediaItem.type == "track",
            or_(direct, _track_has_file(lambda t: t.item_id == MediaItem.id, active_only)),
        ),
        (
            MediaItem.type == "album",
            _track_has_file(lambda t: t.album_id == MediaItem.id, active_only),
        ),
        (
            MediaItem.type == "artist",
            _track_has_file(lambda t: t.artist_id == MediaItem.id, active_only),
        ),
        else_=direct,
    )


@lru_cache(maxsize=1)
def has_any_file() -> ColumnElement[bool]:
    """True when an item (or its children) has at least one file row."""
    return _file_predicate(active_only=False)


@lru_cache(maxsize=1)
def has_active_file() -> ColumnElement[bool]:
    """True when an item (or its children) has at least one non-missing file."""
    return _file_predicate(active_only=True)


@lru_cache(maxsize=1)
def is_visible() -> ColumnElement[bool]:
    """True unless every file backing the item has been soft-deleted.

    Use this as a ``WHERE`` predicate on any query over :class:`MediaItem` to
    hide fully-missing items while keeping items that still have an available
    file, and items that have no file rows at all.

    The expression is expensive to build, so the clause object is cached and
    shared by every query. SQLAlchemy treats clause elements as immutable.
    """
    return or_(not_(has_any_file()), has_active_file())
