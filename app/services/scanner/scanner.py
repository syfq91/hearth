"""Library scanning: reconciliation, change detection and soft deletes."""

import asyncio
import logging
import os
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.ids import new_id
from app.db.base import utcnow
from app.db.models import (
    Episode,
    Library,
    LibraryPath,
    MediaFile,
    MediaItem,
    Movie,
    Season,
    Series,
)
from app.services.media.probe import ProbeResult, probe_media
from app.services.media.titles import normalize_title, sort_title
from app.services.scanner.hashing import partial_hash
from app.services.scanner.parsing import ParsedMovie, parse_episode_path, parse_movie_path
from app.services.scanner.walker import ScannedFile, scope_dirs, walk_movie_files

if TYPE_CHECKING:
    from app.services.metadata.matching import MetadataMatcher

logger = logging.getLogger("hearth.scanner")

ProbeFn = Callable[[Path], Awaitable[ProbeResult]]
Clock = Callable[[], datetime]
Parser = Callable[[Path], ParsedMovie]


class ScanError(Exception):
    """A scan could not start or was asked to scan an unsupported library."""


class ScanCancelled(Exception):
    """Cancellation was observed between scan phases."""


@dataclass
class ScanStats:
    """Counts and timings returned by one scan."""

    library_id: str
    added: int = 0
    added_items: int = 0
    updated: int = 0
    moved: int = 0
    restored: int = 0
    missing: int = 0
    removed: int = 0
    unchanged: int = 0
    probes: int = 0
    errors: int = 0
    matched: int = 0
    unmatched: int = 0
    metadata_pending: int = 0
    provider_calls: int = 0
    images_stored: int = 0
    aborted: bool = False
    abort_reason: str | None = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _ExistingFile:
    id: str
    item_id: str
    path: str
    size_bytes: int
    mtime: float
    partial_hash: str
    missing_since: datetime | None


@dataclass
class _ProbeOutcome:
    result: ProbeResult | None = None
    error: str | None = None


class LibraryScanner:
    """Reconcile one library's files with the catalog.

    The walk, hash and probe phases never hold a database transaction. Only a
    short final transaction writes results, because SQLite has one writer.
    """

    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        probe: ProbeFn | None = None,
        clock: Clock = utcnow,
        parser: Parser = parse_movie_path,
        hash_file: Callable[[Path], str] = partial_hash,
        matcher: "MetadataMatcher | None" = None,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.clock = clock
        self.parser = parser
        self.hash_file = hash_file
        self.matcher = matcher
        self.probe = probe or self._default_probe

    async def _default_probe(self, path: Path) -> ProbeResult:
        return await probe_media(
            path,
            ffprobe_binary=self.settings.ffprobe_binary,
            timeout=self.settings.ffprobe_timeout_seconds,
        )

    async def scan_library(
        self,
        library_id: str,
        *,
        targets: Sequence[str] | None = None,
        progress: Callable[[float], Awaitable[None]] | None = None,
        is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    ) -> ScanStats:
        """Scan a movie library, optionally limited to changed paths.

        ``targets`` are absolute files or directories reported by the watcher;
        removals are only considered inside their resulting scope. Returns
        statistics even when the mass-deletion safeguard aborts the scan.
        """
        started = time.monotonic()
        stats = ScanStats(library_id=library_id)
        await self._set_status(library_id, "running")
        try:
            roots, existing, item_index, tv_indices, library_type = await self._load_library(
                library_id
            )
            await self._check_cancel(is_cancelled)

            scopes = scope_dirs(roots, targets) if targets is not None else None
            if targets is not None and not scopes:
                stats.duration_seconds = round(time.monotonic() - started, 3)
                await self._set_status(library_id, "ok", stats=stats)
                return stats

            if progress:
                await progress(0.05)
            candidates = await asyncio.to_thread(walk_movie_files, roots, start_dirs=scopes)
            await self._check_cancel(is_cancelled)
            if progress:
                await progress(0.15)

            present = {candidate.path for candidate in candidates}
            existing_by_path = {row.path: row for row in existing.values()}

            new_files: list[ScannedFile] = []
            changed_files: list[ScannedFile] = []
            restored_files: list[_ExistingFile] = []
            for candidate in candidates:
                current = existing_by_path.get(candidate.path)
                if current is None:
                    new_files.append(candidate)
                elif current.missing_since is not None:
                    if (
                        current.size_bytes == candidate.size_bytes
                        and current.mtime == candidate.mtime
                    ):
                        restored_files.append(current)
                        stats.restored += 1
                    else:
                        changed_files.append(candidate)
                        stats.restored += 1
                elif current.size_bytes != candidate.size_bytes or current.mtime != candidate.mtime:
                    changed_files.append(candidate)
                else:
                    stats.unchanged += 1

            to_hash = new_files + changed_files
            hashes = await self._hash_files(to_hash, stats, is_cancelled)
            await self._check_cancel(is_cancelled)

            # Pair new paths with files whose old path vanished. A duplicate
            # copy cannot be mistaken for a move while the original still
            # exists, because only absent paths enter the candidate index.
            missing_by_size: dict[int, list[_ExistingFile]] = defaultdict(list)
            for row in existing.values():
                if row.path in present or not self._in_scope(row.path, scopes):
                    continue
                missing_by_size[row.size_bytes].append(row)
            for bucket in missing_by_size.values():
                bucket.sort(key=lambda row: (row.missing_since is not None, row.path))

            moves: dict[str, tuple[_ExistingFile, ScannedFile]] = {}
            moved_ids: set[str] = set()
            for candidate in sorted(new_files, key=lambda item: item.path):
                digest = hashes.get(candidate.path)
                if digest is None:
                    continue
                for row in missing_by_size.get(candidate.size_bytes, []):
                    if row.id in moved_ids:
                        continue
                    if row.partial_hash == digest:
                        moves[candidate.path] = (row, candidate)
                        moved_ids.add(row.id)
                        break

            probe_queue = [
                candidate
                for candidate in new_files
                if candidate.path not in moves and candidate.path in hashes
            ] + [candidate for candidate in changed_files if candidate.path in hashes]
            outcomes = await self._probe_files(probe_queue, stats, is_cancelled)
            await self._check_cancel(is_cancelled)
            if progress:
                await progress(0.70)

            moved_paths = set(moves)
            missing_now = [
                row
                for row in existing.values()
                if row.missing_since is None
                and row.path not in present
                and row.id not in moved_ids
                and self._in_scope(row.path, scopes)
            ]
            active_in_scope = [
                row
                for row in existing.values()
                if row.missing_since is None and self._in_scope(row.path, scopes)
            ]

            if missing_now and len(active_in_scope) >= self.settings.scan_mass_delete_min_files:
                ratio = len(missing_now) / len(active_in_scope)
                if ratio > self.settings.scan_mass_delete_threshold:
                    stats.aborted = True
                    stats.abort_reason = (
                        f"Refusing to remove {len(missing_now)} of {len(active_in_scope)} "
                        f"files ({ratio:.0%}); mass-deletion safeguard triggered"
                    )
                    stats.duration_seconds = round(time.monotonic() - started, 3)
                    await self._set_status(
                        library_id, "aborted", stats=stats, error=stats.abort_reason
                    )
                    logger.warning(
                        "scan aborted by mass-deletion safeguard",
                        extra={"library_id": library_id, "removed": len(missing_now)},
                    )
                    return stats

            now = self.clock()
            grace = timedelta(hours=self.settings.scan_missing_grace_hours)
            expired = [
                row
                for row in existing.values()
                if row.missing_since is not None
                and row.path not in present
                and row.id not in moved_ids
                and self._in_scope(row.path, scopes)
                and now - row.missing_since >= grace
            ]
            delete_now = expired + (missing_now if grace <= timedelta(0) else [])
            mark_missing = missing_now if grace > timedelta(0) else []

            await self._write_changes(
                library_id=library_id,
                library_type=library_type,
                existing=existing,
                item_index=item_index,
                tv_indices=tv_indices,
                new_files=[c for c in new_files if c.path not in moved_paths],
                changed_files=changed_files,
                restored_files=restored_files,
                moves=moves,
                outcomes=outcomes,
                hashes=hashes,
                mark_missing=mark_missing,
                delete_now=delete_now,
                now=now,
                stats=stats,
            )
            await self._check_cancel(is_cancelled)

            if self.matcher is not None:
                await self._run_matching(library_id, stats, is_cancelled)

            if progress:
                await progress(1.0)

            stats.duration_seconds = round(time.monotonic() - started, 3)
            await self._set_status(library_id, "ok", stats=stats)
            logger.info("library scan complete", extra=stats.to_dict())
            return stats
        except ScanCancelled:
            stats.duration_seconds = round(time.monotonic() - started, 3)
            await self._set_status(library_id, "cancelled", stats=stats)
            raise
        except Exception as exc:
            stats.duration_seconds = round(time.monotonic() - started, 3)
            await self._set_status(library_id, "error", stats=stats, error=str(exc))
            raise

    async def _run_matching(
        self,
        library_id: str,
        stats: ScanStats,
        is_cancelled: Callable[[], Awaitable[bool]] | None,
    ) -> None:
        """Match due items without letting a metadata bug fail the scan."""

        assert self.matcher is not None
        try:
            match_stats = await self.matcher.match_pending(
                library_id=library_id,
                is_cancelled=is_cancelled,
            )
        except Exception:
            stats.errors += 1
            logger.exception("metadata matching failed", extra={"library_id": library_id})
            return
        stats.matched += match_stats.matched
        stats.unmatched += match_stats.unmatched
        stats.metadata_pending += match_stats.pending
        stats.provider_calls += match_stats.provider_calls
        stats.images_stored += match_stats.images_stored

    async def _load_library(
        self, library_id: str
    ) -> tuple[
        list[str],
        dict[str, _ExistingFile],
        dict[tuple[str, int], str],
        tuple[
            dict[tuple[str, int], str],
            dict[tuple[str, int], str],
            dict[tuple[str, int, int], str],
        ]
        | None,
        str,
    ]:
        async with self.sessionmaker() as session:
            library = await session.get(Library, library_id)
            if library is None:
                raise ScanError(f"Library {library_id} does not exist")
            if library.type not in ("movie", "tv"):
                raise ScanError("Only movie and tv libraries can be scanned in this phase")

            roots = list(
                await session.scalars(
                    select(LibraryPath.path)
                    .where(LibraryPath.library_id == library_id)
                    .order_by(LibraryPath.path)
                )
            )

            rows = (
                await session.execute(
                    select(
                        MediaFile.id,
                        MediaFile.item_id,
                        MediaFile.path,
                        MediaFile.size_bytes,
                        MediaFile.mtime,
                        MediaFile.partial_hash,
                        MediaFile.missing_since,
                    )
                    .join(MediaItem, MediaItem.id == MediaFile.item_id)
                    .where(MediaItem.library_id == library_id)
                )
            ).all()

            existing = {
                row.path: _ExistingFile(
                    id=row.id,
                    item_id=row.item_id,
                    path=row.path,
                    size_bytes=row.size_bytes,
                    mtime=row.mtime,
                    partial_hash=row.partial_hash,
                    missing_since=row.missing_since,
                )
                for row in rows
            }

            if library.type == "movie":
                items = (
                    await session.execute(
                        select(MediaItem.id, MediaItem.title, MediaItem.year).where(
                            MediaItem.library_id == library_id, MediaItem.type == "movie"
                        )
                    )
                ).all()
                item_index = {(normalize_title(row.title), row.year or 0): row.id for row in items}
                tv_indices = None
            else:
                item_index = {}
                series_rows = (
                    await session.execute(
                        select(MediaItem.id, MediaItem.title, MediaItem.year).where(
                            MediaItem.library_id == library_id, MediaItem.type == "series"
                        )
                    )
                ).all()
                series_index = {
                    (normalize_title(row.title), row.year or 0): row.id for row in series_rows
                }
                season_rows = (
                    await session.execute(
                        select(Season.item_id, Season.series_id, Season.season_number)
                        .join(MediaItem, MediaItem.id == Season.item_id)
                        .where(MediaItem.library_id == library_id)
                    )
                ).all()
                season_index = {
                    (row.series_id, row.season_number): row.item_id for row in season_rows
                }
                ep_rows = (
                    await session.execute(
                        select(
                            Episode.item_id,
                            Episode.series_id,
                            Episode.season_number,
                            Episode.episode_number,
                        )
                        .join(MediaItem, MediaItem.id == Episode.item_id)
                        .where(MediaItem.library_id == library_id)
                    )
                ).all()
                episode_index = {
                    (row.series_id, row.season_number, row.episode_number): row.item_id
                    for row in ep_rows
                    if row.season_number is not None and row.episode_number is not None
                }
                tv_indices = (series_index, season_index, episode_index)

        return roots, existing, item_index, tv_indices, library.type

    async def _hash_files(
        self,
        candidates: Sequence[ScannedFile],
        stats: ScanStats,
        is_cancelled: Callable[[], Awaitable[bool]] | None,
    ) -> dict[str, str]:
        semaphore = asyncio.Semaphore(max(1, self.settings.ffprobe_concurrency))

        async def one(candidate: ScannedFile) -> tuple[str, str | None]:
            async with semaphore:
                try:
                    return candidate.path, await asyncio.to_thread(
                        self.hash_file, Path(candidate.path)
                    )
                except OSError as exc:
                    stats.errors += 1
                    logger.warning(
                        "could not hash media file",
                        extra={"path": candidate.path, "error": str(exc)},
                    )
                    return candidate.path, None

        results = await asyncio.gather(*(one(candidate) for candidate in candidates))
        await self._check_cancel(is_cancelled)
        return {path: digest for path, digest in results if digest}

    async def _probe_files(
        self,
        candidates: Sequence[ScannedFile],
        stats: ScanStats,
        is_cancelled: Callable[[], Awaitable[bool]] | None,
    ) -> dict[str, _ProbeOutcome]:
        semaphore = asyncio.Semaphore(max(1, self.settings.ffprobe_concurrency))

        async def one(candidate: ScannedFile) -> tuple[str, _ProbeOutcome]:
            async with semaphore:
                await self._check_cancel(is_cancelled)
                stats.probes += 1
                try:
                    return candidate.path, _ProbeOutcome(
                        result=await self.probe(Path(candidate.path))
                    )
                except Exception as exc:  # one corrupt file must not fail a scan
                    stats.errors += 1
                    logger.warning(
                        "media probe failed",
                        extra={"path": candidate.path, "error": str(exc)},
                    )
                    return candidate.path, _ProbeOutcome(error=str(exc))

        results = await asyncio.gather(*(one(candidate) for candidate in candidates))
        return dict(results)

    async def _write_changes(
        self,
        *,
        library_id: str,
        library_type: str,
        existing: dict[str, _ExistingFile],
        item_index: dict[tuple[str, int], str],
        tv_indices: (
            tuple[
                dict[tuple[str, int], str],
                dict[tuple[str, int], str],
                dict[tuple[str, int, int], str],
            ]
            | None
        ),
        new_files: Sequence[ScannedFile],
        changed_files: Sequence[ScannedFile],
        restored_files: Sequence[_ExistingFile],
        moves: dict[str, tuple[_ExistingFile, ScannedFile]],
        outcomes: dict[str, _ProbeOutcome],
        hashes: dict[str, str],
        mark_missing: Sequence[_ExistingFile],
        delete_now: Sequence[_ExistingFile],
        now: datetime,
        stats: ScanStats,
    ) -> None:
        async with self.sessionmaker() as session:
            for candidate_path, (old, candidate) in moves.items():
                row = await session.get(MediaFile, old.id)
                if row is None:
                    continue
                row.path = candidate_path
                row.mtime = candidate.mtime
                row.missing_since = None
                stats.moved += 1

            for candidate in changed_files:
                current = existing.get(candidate.path)
                if current is None:
                    continue
                row = await session.get(MediaFile, current.id)
                if row is None:
                    continue
                self._apply_probe(row, outcomes.get(candidate.path))
                row.size_bytes = candidate.size_bytes
                row.mtime = candidate.mtime
                row.partial_hash = hashes.get(candidate.path, current.partial_hash)
                row.missing_since = None
                stats.updated += 1

            for current in restored_files:
                row = await session.get(MediaFile, current.id)
                if row is not None:
                    row.missing_since = None

            if library_type == "movie":
                for candidate in new_files:
                    parsed = self.parser(Path(candidate.path))
                    key = (normalize_title(parsed.title), parsed.year or 0)
                    item_id = item_index.get(key)
                    if item_id is None:
                        item_id = new_id()
                        item = MediaItem(
                            id=item_id,
                            library_id=library_id,
                            type="movie",
                            title=parsed.title,
                            sort_title=parsed.sort_title,
                            year=parsed.year,
                            added_at=now,
                            match_state="pending",
                            next_match_at=now,
                        )
                        item.movie = Movie(edition=parsed.edition, part=parsed.part)
                        session.add(item)
                        item_index[key] = item_id
                        stats.added_items += 1

                    outcome = outcomes.get(candidate.path)
                    file_row = MediaFile(
                        item_id=item_id,
                        path=candidate.path,
                        size_bytes=candidate.size_bytes,
                        mtime=candidate.mtime,
                        partial_hash=hashes.get(candidate.path, ""),
                        version_label=parsed.edition,
                    )
                    self._apply_probe(file_row, outcome)
                    session.add(file_row)
                    stats.added += 1
            else:
                assert tv_indices is not None
                series_index, season_index, episode_index = tv_indices
                for candidate in new_files:
                    parsed_ep = parse_episode_path(Path(candidate.path))
                    s_key = parsed_ep.series_key
                    series_id = series_index.get(s_key)
                    if series_id is None:
                        series_id = new_id()
                        series_item = MediaItem(
                            id=series_id,
                            library_id=library_id,
                            type="series",
                            title=parsed_ep.series_title,
                            sort_title=parsed_ep.series_sort_title,
                            year=parsed_ep.series_year,
                            added_at=now,
                            match_state="pending",
                            next_match_at=now,
                        )
                        series_item.series = Series()
                        session.add(series_item)
                        series_index[s_key] = series_id
                        stats.added_items += 1

                    season_id: str | None = None
                    if parsed_ep.season_number is not None:
                        sea_key = (series_id, parsed_ep.season_number)
                        season_id = season_index.get(sea_key)
                        if season_id is None:
                            season_id = new_id()
                            season_title = (
                                "Specials"
                                if parsed_ep.season_number == 0
                                else f"Season {parsed_ep.season_number}"
                            )
                            season_item = MediaItem(
                                id=season_id,
                                library_id=library_id,
                                type="season",
                                title=season_title,
                                sort_title=sort_title(season_title),
                                added_at=now,
                                match_state="pending",
                                next_match_at=now,
                            )
                            season_item.season = Season(
                                series_id=series_id,
                                season_number=parsed_ep.season_number,
                            )
                            session.add(season_item)
                            season_index[sea_key] = season_id
                            stats.added_items += 1

                    outcome = outcomes.get(candidate.path)
                    file_id = new_id()
                    file_row = MediaFile(
                        id=file_id,
                        item_id="",
                        path=candidate.path,
                        size_bytes=candidate.size_bytes,
                        mtime=candidate.mtime,
                        partial_hash=hashes.get(candidate.path, ""),
                        version_label=parsed_ep.edition,
                    )
                    self._apply_probe(file_row, outcome)

                    if parsed_ep.episode_numbers:
                        first_item_id: str | None = None
                        for ep_num in parsed_ep.episode_numbers:
                            ep_key = (series_id, parsed_ep.season_number or 0, ep_num)
                            ep_item_id = episode_index.get(ep_key)
                            if ep_item_id is None:
                                ep_item_id = new_id()
                                ep_title = parsed_ep.episode_title or f"Episode {ep_num}"
                                ep_item = MediaItem(
                                    id=ep_item_id,
                                    library_id=library_id,
                                    type="episode",
                                    title=ep_title,
                                    sort_title=sort_title(ep_title),
                                    added_at=now,
                                    match_state="pending",
                                    next_match_at=now,
                                )
                                ep_item.episode = Episode(
                                    series_id=series_id,
                                    season_id=season_id,
                                    season_number=parsed_ep.season_number,
                                    episode_number=ep_num,
                                    air_date=parsed_ep.air_date,
                                    absolute_number=parsed_ep.absolute_number,
                                    file_id=file_id,
                                )
                                session.add(ep_item)
                                episode_index[ep_key] = ep_item_id
                                stats.added_items += 1
                            else:
                                ep_row = await session.get(Episode, ep_item_id)
                                if ep_row is not None and ep_row.file_id is None:
                                    ep_row.file_id = file_id

                            if first_item_id is None:
                                first_item_id = ep_item_id

                        file_row.item_id = first_item_id or series_id
                    else:
                        ep_item_id = new_id()
                        ep_title = parsed_ep.episode_title or Path(candidate.path).stem
                        is_matchable = bool(parsed_ep.air_date)
                        ep_item = MediaItem(
                            id=ep_item_id,
                            library_id=library_id,
                            type="episode",
                            title=ep_title,
                            sort_title=sort_title(ep_title),
                            added_at=now,
                            match_state="pending" if is_matchable else "unmatched",
                            next_match_at=now if is_matchable else None,
                        )
                        ep_item.episode = Episode(
                            series_id=series_id,
                            season_id=season_id,
                            season_number=parsed_ep.season_number,
                            episode_number=None,
                            air_date=parsed_ep.air_date,
                            file_id=file_id,
                        )
                        session.add(ep_item)
                        file_row.item_id = ep_item_id
                        stats.added_items += 1

                    session.add(file_row)
                    stats.added += 1

            for current in mark_missing:
                row = await session.get(MediaFile, current.id)
                if row is not None and row.missing_since is None:
                    row.missing_since = now
                    stats.missing += 1

            for current in delete_now:
                row = await session.get(MediaFile, current.id)
                if row is not None:
                    await session.delete(row)
                    stats.removed += 1

            await session.flush()

            if library_type == "movie":
                orphan_items = await session.scalars(
                    select(MediaItem).where(
                        MediaItem.library_id == library_id,
                        ~MediaItem.files.any(),
                    )
                )
                for item in orphan_items:
                    await session.delete(item)
            else:
                orphan_eps = list(
                    await session.scalars(
                        select(Episode)
                        .join(MediaItem, MediaItem.id == Episode.item_id)
                        .where(
                            MediaItem.library_id == library_id,
                            Episode.file_id.is_(None),
                            ~MediaItem.files.any(),
                        )
                    )
                )
                for ep in orphan_eps:
                    orphan_ep_item = await session.get(MediaItem, ep.item_id)
                    if orphan_ep_item is not None:
                        await session.delete(orphan_ep_item)
                await session.flush()

                orphan_seasons = list(
                    await session.scalars(
                        select(Season)
                        .join(MediaItem, MediaItem.id == Season.item_id)
                        .where(
                            MediaItem.library_id == library_id,
                            ~Season.episodes.any(),
                        )
                    )
                )
                for season in orphan_seasons:
                    orphan_season_item = await session.get(MediaItem, season.item_id)
                    if orphan_season_item is not None:
                        await session.delete(orphan_season_item)
                await session.flush()

                orphan_series = list(
                    await session.scalars(
                        select(Series)
                        .join(MediaItem, MediaItem.id == Series.item_id)
                        .where(
                            MediaItem.library_id == library_id,
                            ~Series.episodes.any(),
                        )
                    )
                )
                for s in orphan_series:
                    orphan_series_item = await session.get(MediaItem, s.item_id)
                    if orphan_series_item is not None:
                        await session.delete(orphan_series_item)

            await session.commit()

    @staticmethod
    def _apply_probe(row: MediaFile, outcome: _ProbeOutcome | None) -> None:
        result = outcome.result if outcome else None
        if result is None:
            row.container = None
            row.duration_seconds = None
            row.bit_rate = None
            row.streams_json = {}
            row.probe_error = outcome.error[:2000] if outcome and outcome.error else None
            return
        row.container = result.container
        row.duration_seconds = result.duration_seconds
        row.bit_rate = result.bit_rate
        row.streams_json = result.model_dump(mode="json")
        row.probe_error = None

    @staticmethod
    def _in_scope(path: str, scopes: Sequence[str] | None) -> bool:
        if scopes is None:
            return True
        return any(path == scope or path.startswith(scope + os.sep) for scope in scopes)

    async def _check_cancel(self, is_cancelled: Callable[[], Awaitable[bool]] | None) -> None:
        if is_cancelled is not None and await is_cancelled():
            raise ScanCancelled()

    async def _set_status(
        self,
        library_id: str,
        status: str,
        *,
        stats: ScanStats | None = None,
        error: str | None = None,
    ) -> None:
        async with self.sessionmaker() as session:
            await session.execute(
                update(Library)
                .where(Library.id == library_id)
                .values(
                    last_scan_at=self.clock(),
                    last_scan_status=status,
                    last_scan_error=error,
                    last_scan_stats=stats.to_dict() if stats else {},
                )
            )
            await session.commit()
