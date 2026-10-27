"""Match catalog items to local or remote metadata.

The matcher is deliberately provider-agnostic: the scanner can pass a matcher in
and call :meth:`MetadataMatcher.match_pending` after a scan, while the manual
match endpoint calls :meth:`MetadataMatcher.manual_match`. Provider failures are
recorded on the item as ``pending`` with an exponential retry time, never raised
into the scan.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Literal, cast

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.ids import new_id
from app.db.base import utcnow
from app.db.models import (
    Author,
    Book,
    Episode,
    Genre,
    ItemGenre,
    ItemPerson,
    MediaFile,
    MediaItem,
    Movie,
    Person,
    Season,
    Track,
)
from app.db.models import (
    Image as ImageModel,
)
from app.services.media.titles import normalize_title, sort_title
from app.services.metadata.base import (
    BookMetadata,
    EpisodeMetadata,
    MetadataProvider,
    MovieMetadata,
    PersonCredit,
    ProviderError,
    ProviderImage,
    ProviderUnavailable,
    SearchResult,
)
from app.services.metadata.googlebooks import GoogleBooksProvider
from app.services.metadata.local import (
    LocalProvider,
    find_local_book_cover,
    find_local_book_opf,
    parse_local_opf,
)
from app.services.metadata.musicbrainz import MusicBrainzProvider
from app.services.metadata.openlibrary import OpenLibraryProvider
from app.services.metadata.tmdb import TMDBProvider
from app.services.scanner.walker import find_artist_artwork

logger = logging.getLogger("hearth.metadata.matching")

MatchState = Literal["matched", "unmatched", "pending"]
ArtworkHandler = Callable[[str, str, Sequence[ProviderImage]], Awaitable[int]]

_TITLE_WEIGHT = 0.60
_YEAR_WEIGHT = 0.25
_RUNTIME_WEIGHT = 0.15


@dataclass
class MatchResult:
    """Outcome of matching one item."""

    item_id: str
    state: MatchState
    provider: str | None = None
    provider_id: str | None = None
    score: float | None = None
    error: str | None = None
    images: int = 0


@dataclass
class MatchStats:
    """Aggregate counters for a batch of match attempts."""

    attempted: int = 0
    matched: int = 0
    unmatched: int = 0
    pending: int = 0
    provider_calls: int = 0
    images_stored: int = 0
    results: list[MatchResult] = field(default_factory=list)


@dataclass(frozen=True)
class _ItemSnapshot:
    id: str
    library_id: str
    title: str
    year: int | None
    match_state: str
    file_paths: tuple[str, ...]
    duration_seconds: float | None
    type: str = "movie"
    artist_name: str | None = None
    musicbrainz_id: str | None = None
    author_name: str | None = None
    isbn: str | None = None


def _year_score(left: int | None, right: int | None) -> float:
    if left is None or right is None:
        return 0.5
    difference = abs(left - right)
    if difference == 0:
        return 1.0
    if difference == 1:
        return 0.7
    return 0.0


def _runtime_score(local: float | None, remote: int | None) -> float:
    if not local or not remote:
        return 0.6
    ratio = min(local, remote) / max(local, remote)
    if ratio >= 0.95:
        return 1.0
    if ratio >= 0.85:
        return 0.8
    if ratio >= 0.70:
        return 0.5
    return 0.0


def score_candidate(
    query_title: str,
    query_year: int | None,
    candidate_title: str,
    candidate_year: int | None,
    *,
    runtime_seconds: float | None = None,
    candidate_runtime_seconds: int | None = None,
) -> float:
    """Score one candidate against a parsed title, year and runtime.

    Title similarity dominates; the year and runtime provide the guard rails
    that keep a same-titled remake from winning.
    """

    title_score = SequenceMatcher(
        None, normalize_title(query_title), normalize_title(candidate_title)
    ).ratio()
    return (
        _TITLE_WEIGHT * title_score
        + _YEAR_WEIGHT * _year_score(query_year, candidate_year)
        + _RUNTIME_WEIGHT * _runtime_score(runtime_seconds, candidate_runtime_seconds)
    )


class MetadataMatcher:
    """Resolve pending catalog items against local and remote providers."""

    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        providers: Sequence[MetadataProvider] | None = None,
        artwork: ArtworkHandler | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.artwork = artwork
        self.clock = clock
        self._provider_calls = 0

        if providers is None:
            self.providers: list[MetadataProvider] = [LocalProvider()]
            if settings.tmdb_api_key:
                self.providers.append(TMDBProvider(settings, sessionmaker))
            if settings.musicbrainz_enabled:
                self.providers.append(MusicBrainzProvider(settings, sessionmaker))
            if settings.openlibrary_enabled:
                self.providers.append(OpenLibraryProvider(settings))
            if settings.google_books_enabled:
                self.providers.append(GoogleBooksProvider(settings))
        else:
            self.providers = list(providers)

        self.local_provider = next(
            (provider for provider in self.providers if provider.name == "local"), None
        )
        self.remote_providers = [
            provider for provider in self.providers if provider.name != "local"
        ]
        self._tmdb = next(
            (provider for provider in self.remote_providers if provider.name == "tmdb"),
            None,
        )
        self._musicbrainz = next(
            (provider for provider in self.remote_providers if provider.name == "musicbrainz"),
            None,
        )
        self._openlibrary = next(
            (provider for provider in self.remote_providers if provider.name == "openlibrary"),
            None,
        )
        self._googlebooks = next(
            (provider for provider in self.remote_providers if provider.name == "googlebooks"),
            None,
        )

    # ------------------------------------------------------------------ public

    async def match_pending(
        self,
        *,
        library_id: str | None = None,
        limit: int = 100,
        progress: Callable[[float], Awaitable[None]] | None = None,
        is_cancelled: Callable[[], Awaitable[bool]] | None = None,
    ) -> MatchStats:
        """Match due ``pending``/``unmatched`` items, newest additions first."""

        now = self.clock()
        query = (
            select(MediaItem.id)
            .where(
                MediaItem.type.in_(("movie", "series", "artist", "album")),
                MediaItem.match_state.in_(("pending", "unmatched")),
                or_(MediaItem.next_match_at.is_(None), MediaItem.next_match_at <= now),
                or_(
                    MediaItem.type.in_(("series", "artist", "album")),
                    MediaItem.files.any(MediaFile.missing_since.is_(None)),
                ),
            )
            .order_by(MediaItem.added_at, MediaItem.id)
            .limit(limit)
        )
        if library_id is not None:
            query = query.where(MediaItem.library_id == library_id)

        async with self.sessionmaker() as session:
            item_ids = list(await session.scalars(query))

        stats = MatchStats()
        before_calls = self._provider_calls
        for index, item_id in enumerate(item_ids):
            if is_cancelled is not None and await is_cancelled():
                break
            result = await self.match_item(item_id)
            stats.results.append(result)
            stats.attempted += 1
            if result.state == "matched":
                stats.matched += 1
                stats.images_stored += result.images
            elif result.state == "unmatched":
                stats.unmatched += 1
            else:
                stats.pending += 1
            if progress is not None and item_ids:
                await progress((index + 1) / len(item_ids))
        stats.provider_calls = self._provider_calls - before_calls
        return stats

    async def match_item(self, item_id: str, *, force: bool = False) -> MatchResult:
        """Match one item by id, leaving it retryable on any provider failure."""

        snapshot = await self._load_snapshot(item_id)
        if snapshot is None:
            return MatchResult(item_id=item_id, state="pending", error="Item not found")
        if snapshot.match_state == "matched" and not force:
            return MatchResult(item_id=item_id, state="matched")

        if snapshot.type == "series":
            return await self._match_series(snapshot)
        if snapshot.type == "artist":
            return await self._match_artist(snapshot)
        if snapshot.type == "album":
            return await self._match_album(snapshot)
        if snapshot.type == "book":
            return await self._match_book(snapshot)

        local_error: str | None = None
        if self.local_provider is not None:
            try:
                details = await self._local_details(snapshot)
            except ProviderError as exc:
                local_error = str(exc)
                logger.warning(
                    "local metadata lookup failed",
                    extra={"item_id": item_id, "error": local_error},
                )
                details = None
            if details is not None:
                return await self._apply(
                    snapshot,
                    details.provider,
                    details.provider_id,
                    details,
                    list(details.images),
                    None,
                )

        remote = self._tmdb or (self.remote_providers[0] if self.remote_providers else None)
        if remote is None:
            message = local_error or "TMDB API key is not configured"
            return await self._save_failure(item_id, message)

        try:
            return await self._match_remote(snapshot, remote)
        except (ProviderUnavailable, ProviderError) as exc:
            return await self._save_failure(item_id, str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # defensive: a bad payload must not fail a scan
            logger.exception("metadata match crashed", extra={"item_id": item_id})
            return await self._save_failure(item_id, f"Match failed: {type(exc).__name__}")

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        """Search every provider, returning partial results when one fails."""

        results: list[SearchResult] = []
        errors: list[ProviderError] = []
        for provider in self.providers:
            self._provider_calls += 1
            try:
                results.extend(await provider.search(query, year))
            except ProviderError as exc:
                errors.append(exc)
        if not results and errors:
            raise errors[0]
        results.sort(key=lambda result: result.popularity, reverse=True)
        return results

    async def manual_match(self, item_id: str, provider_name: str, provider_id: str) -> MatchResult:
        """Attach a specific provider candidate to an item, bypassing scoring."""

        snapshot = await self._load_snapshot(item_id)
        if snapshot is None:
            return MatchResult(item_id=item_id, state="pending", error="Item not found")

        if snapshot.type == "series":
            return await self._apply_series_match(snapshot, provider_name, provider_id, score=1.0)

        provider = next(
            (candidate for candidate in self.providers if candidate.name == provider_name),
            None,
        )
        if provider is None:
            raise ProviderUnavailable(f"Provider {provider_name!r} is not configured")

        self._provider_calls += 1
        details = await provider.fetch_details(provider_id)
        if details is None:
            raise ProviderError(f"Provider {provider_name!r} has no item {provider_id!r}")

        images = list(details.images)
        if not images:
            self._provider_calls += 1
            try:
                images = await provider.fetch_images(provider_id)
            except ProviderError:
                logger.warning(
                    "image lookup failed during manual match",
                    extra={"item_id": item_id, "provider": provider_name},
                )

        return await self._apply(snapshot, provider_name, provider_id, details, images, None)

    async def close(self) -> None:
        """Release clients owned by the providers and the artwork handler."""

        for provider in self.providers:
            try:
                await provider.close()
            except Exception:  # pragma: no cover - close must never mask a scan
                logger.warning(
                    "failed to close metadata provider", extra={"provider": provider.name}
                )

        close = getattr(self.artwork, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:  # pragma: no cover - close must never mask a scan
                logger.warning("failed to close artwork handler")

    # --------------------------------------------------------------- internals

    async def _load_snapshot(self, item_id: str) -> _ItemSnapshot | None:
        async with self.sessionmaker() as session:
            item = await session.get(MediaItem, item_id)
            if item is None:
                return None
            active = sorted(
                (file for file in item.files if file.missing_since is None),
                key=lambda file: file.path,
            )
            durations = [file.duration_seconds for file in active if file.duration_seconds]
            file_paths: list[str] = [file.path for file in active]
            artist_name: str | None = None
            musicbrainz_id: str | None = None

            author_name: str | None = None
            isbn: str | None = None

            if item.type == "artist":
                if item.artist is not None:
                    musicbrainz_id = item.artist.musicbrainz_id
                track_files = list(
                    await session.scalars(
                        select(MediaFile.path)
                        .join(Track, Track.file_id == MediaFile.id)
                        .where(Track.artist_id == item.id, MediaFile.missing_since.is_(None))
                        .limit(5)
                    )
                )
                file_paths.extend(track_files)
            elif item.type == "album":
                if item.album is not None:
                    musicbrainz_id = item.album.musicbrainz_id
                    artist_name = item.album.album_artist
                    if not artist_name and item.album.artist is not None:
                        artist_item = await session.get(MediaItem, item.album.artist.item_id)
                        if artist_item is not None:
                            artist_name = artist_item.title
                track_files = list(
                    await session.scalars(
                        select(MediaFile.path)
                        .join(Track, Track.file_id == MediaFile.id)
                        .where(Track.album_id == item.id, MediaFile.missing_since.is_(None))
                        .limit(5)
                    )
                )
                file_paths.extend(track_files)
            elif item.type == "book":
                if item.book is not None:
                    isbn = item.book.isbn
                    if item.book.author is not None:
                        author_name = item.book.author.name

            return _ItemSnapshot(
                id=item.id,
                library_id=item.library_id,
                title=item.title,
                year=item.year,
                match_state=item.match_state,
                file_paths=tuple(file_paths),
                duration_seconds=max(durations) if durations else None,
                type=item.type,
                artist_name=artist_name,
                musicbrainz_id=musicbrainz_id,
                author_name=author_name,
                isbn=isbn,
            )

    async def _local_details(self, snapshot: _ItemSnapshot) -> MovieMetadata | None:
        provider = self.local_provider
        if provider is None:
            return None
        for path in snapshot.file_paths:
            details = cast(
                "MovieMetadata | None",
                await asyncio.to_thread(provider.find_movie, Path(path)),  # type: ignore[attr-defined]
            )
            if details is not None:
                return details
        return None

    async def _match_remote(
        self, snapshot: _ItemSnapshot, provider: MetadataProvider
    ) -> MatchResult:
        self._provider_calls += 1
        candidates = await provider.search(snapshot.title, snapshot.year)
        if not candidates:
            return await self._save_unmatched(snapshot.id, "No provider candidates")

        ranked = sorted(
            candidates,
            key=lambda candidate: score_candidate(
                snapshot.title,
                snapshot.year,
                candidate.title,
                candidate.year,
            ),
            reverse=True,
        )

        best_score = 0.0
        best: tuple[SearchResult, MovieMetadata] | None = None
        for candidate in ranked[:3]:
            self._provider_calls += 1
            details = await provider.fetch_details(candidate.provider_id)
            if details is None:
                continue
            candidate_score = score_candidate(
                snapshot.title,
                snapshot.year,
                details.title,
                details.year,
                runtime_seconds=snapshot.duration_seconds,
                candidate_runtime_seconds=details.runtime_seconds,
            )
            if candidate_score > best_score:
                best_score = candidate_score
                best = (candidate, details)

        if best is None or best_score < self.settings.metadata_match_threshold:
            label = best[1].title if best else "unknown"
            return await self._save_unmatched(
                snapshot.id, f"No confident match (best {best_score:.2f}: {label})"
            )

        candidate, details = best
        images = list(details.images)
        if not images:
            self._provider_calls += 1
            try:
                images = await provider.fetch_images(candidate.provider_id)
            except ProviderError:
                logger.warning(
                    "image lookup failed during match",
                    extra={"item_id": snapshot.id, "provider": provider.name},
                )
        return await self._apply(
            snapshot, provider.name, candidate.provider_id, details, images, best_score
        )

    async def _apply(
        self,
        snapshot: _ItemSnapshot,
        provider_name: str,
        provider_id: str,
        metadata: MovieMetadata,
        images: Sequence[ProviderImage],
        score: float | None,
    ) -> MatchResult:
        now = self.clock()
        async with self.sessionmaker() as session:
            item = await session.get(MediaItem, snapshot.id)
            if item is None:
                return MatchResult(item_id=snapshot.id, state="pending", error="Item not found")

            item.title = metadata.title or item.title
            item.sort_title = sort_title(item.title)
            item.year = metadata.year if metadata.year is not None else item.year
            item.overview = metadata.overview or item.overview
            item.rating = metadata.rating if metadata.rating is not None else item.rating
            item.runtime_seconds = (
                metadata.runtime_seconds
                if metadata.runtime_seconds is not None
                else item.runtime_seconds
            )
            item.match_state = "matched"
            item.match_attempts += 1
            item.match_error = None
            item.matched_at = now
            item.next_match_at = None

            provider_ids = dict(item.provider_ids or {})
            provider_ids.update(metadata.provider_ids)
            if provider_name == "local":
                provider_ids["nfo"] = provider_id
            else:
                provider_ids[provider_name] = provider_id
            item.provider_ids = provider_ids

            movie = item.movie
            if movie is None:
                movie = Movie(item_id=item.id)
                item.movie = movie
            if metadata.original_title:
                movie.original_title = metadata.original_title
            if metadata.release_date:
                movie.release_date = metadata.release_date
            if metadata.tagline:
                movie.tagline = metadata.tagline

            await self._apply_genres(session, item.id, metadata.genres)
            await self._apply_credits(session, item.id, metadata.credits)

            if metadata.sidecar_subtitles:
                files = await session.scalars(
                    select(MediaFile).where(
                        MediaFile.item_id == item.id,
                        MediaFile.missing_since.is_(None),
                    )
                )
                for file in files:
                    file.sidecar_subtitles = list(metadata.sidecar_subtitles)

            await session.commit()

        images_stored = await self._store_images(snapshot.id, provider_name, images)
        logger.info(
            "matched item",
            extra={
                "item_id": snapshot.id,
                "provider": provider_name,
                "score": score,
            },
        )
        return MatchResult(
            item_id=snapshot.id,
            state="matched",
            provider=provider_name,
            provider_id=provider_id,
            score=score,
            images=images_stored,
        )

    async def _match_series(self, snapshot: _ItemSnapshot) -> MatchResult:
        if self.local_provider is not None:
            for file_path in snapshot.file_paths:
                p = Path(file_path).parent
                for candidate_dir in (p, p.parent):
                    nfo = candidate_dir / "tvshow.nfo"
                    if nfo.is_file():
                        try:
                            local_meta = (
                                self.local_provider.find_series(nfo)
                                if hasattr(self.local_provider, "find_series")
                                else None
                            )
                        except ProviderError:
                            local_meta = None
                        if local_meta is not None:
                            return await self._apply_series_match(
                                snapshot,
                                self.local_provider.name,
                                str(nfo),
                                score=1.0,
                            )

        provider = self._tmdb or next(
            (p for p in self.remote_providers if hasattr(p, "search_tv")), None
        )
        if provider is None:
            return await self._save_failure(snapshot.id, "No TV provider available")

        self._provider_calls += 1
        try:
            candidates = await provider.search_tv(snapshot.title, year=snapshot.year)
        except (ProviderUnavailable, ProviderError) as exc:
            return await self._save_failure(snapshot.id, str(exc))

        if not candidates:
            return await self._save_unmatched(snapshot.id, "No candidates returned by provider")

        scored: list[tuple[float, SearchResult]] = []
        for candidate in candidates:
            score = score_candidate(snapshot.title, snapshot.year, candidate.title, candidate.year)
            scored.append((score, candidate))
        scored.sort(key=lambda pair: pair[0], reverse=True)

        best_score, best_candidate = scored[0]
        if best_score < self.settings.metadata_match_threshold:
            threshold = self.settings.metadata_match_threshold
            return await self._save_unmatched(
                snapshot.id,
                f"Best candidate {best_candidate.title!r} scored {best_score:.2f} "
                f"(threshold {threshold:.2f})",
            )

        return await self._apply_series_match(
            snapshot, provider.name, best_candidate.provider_id, score=best_score
        )

    async def _apply_series_match(
        self,
        snapshot: _ItemSnapshot,
        provider_name: str,
        provider_id: str,
        score: float,
    ) -> MatchResult:
        provider = next(
            (candidate for candidate in self.providers if candidate.name == provider_name),
            None,
        )
        if provider is None:
            raise ProviderUnavailable(f"Provider {provider_name!r} is not configured")

        self._provider_calls += 1
        series_meta = await provider.fetch_tv_details(provider_id)
        if series_meta is None:
            raise ProviderError(f"Provider {provider_name!r} has no series {provider_id!r}")

        images = list(series_meta.images)
        if not images:
            self._provider_calls += 1
            try:
                images = await provider.fetch_tv_images(provider_id)
            except ProviderError:
                images = []

        now = self.clock()
        async with self.sessionmaker() as session:
            item = await session.get(MediaItem, snapshot.id)
            if item is None:
                return MatchResult(item_id=snapshot.id, state="pending", error="Item not found")

            item.title = series_meta.title
            item.sort_title = sort_title(series_meta.title)
            item.year = series_meta.year
            item.overview = series_meta.overview
            item.rating = series_meta.rating
            item.provider_ids = dict(series_meta.provider_ids)
            item.match_state = "matched"
            item.matched_at = now
            item.next_match_at = None
            item.match_error = None
            if item.series is not None:
                item.series.original_title = series_meta.original_title
                item.series.first_air_date = series_meta.first_air_date
                item.series.status = series_meta.status
                item.series.network = series_meta.network

            await self._apply_genres(session, item.id, series_meta.genres)
            await self._apply_credits(session, item.id, series_meta.credits)

            # Match seasons and episodes
            seasons = list(
                await session.scalars(select(Season).where(Season.series_id == snapshot.id))
            )
            for season in seasons:
                self._provider_calls += 1
                try:
                    season_meta = await provider.fetch_tv_season(provider_id, season.season_number)
                except ProviderError:
                    season_meta = None

                sea_item = await session.get(MediaItem, season.item_id)
                if season_meta is not None:
                    if sea_item:
                        sea_item.title = season_meta.title or f"Season {season.season_number}"
                        sea_item.overview = season_meta.overview
                        sea_item.match_state = "matched"
                        sea_item.matched_at = now
                        if season_meta.images:
                            await self._store_images(sea_item.id, provider_name, season_meta.images)

                    tmdb_by_num = {
                        ep.episode_number: ep
                        for ep in season_meta.episodes
                        if ep.episode_number is not None
                    }
                    tmdb_by_date = {ep.air_date: ep for ep in season_meta.episodes if ep.air_date}

                    episodes = list(
                        await session.scalars(
                            select(Episode).where(Episode.season_id == season.item_id)
                        )
                    )
                    for ep in episodes:
                        ep_meta: EpisodeMetadata | None = None
                        if ep.episode_number is not None and ep.episode_number in tmdb_by_num:
                            ep_meta = tmdb_by_num[ep.episode_number]
                        elif ep.air_date and ep.air_date in tmdb_by_date:
                            ep_meta = tmdb_by_date[ep.air_date]
                            ep.episode_number = ep_meta.episode_number
                        elif self.local_provider is not None and ep.file:
                            try:
                                ep_meta = (
                                    self.local_provider.find_episode(ep.file.path)
                                    if hasattr(self.local_provider, "find_episode")
                                    else None
                                )
                            except ProviderError:
                                ep_meta = None

                        ep_item = await session.get(MediaItem, ep.item_id)
                        if ep_item is not None:
                            if ep_meta is not None:
                                ep_item.title = ep_meta.title
                                ep_item.sort_title = sort_title(ep_meta.title)
                                ep_item.overview = ep_meta.overview
                                ep_item.runtime_seconds = ep_meta.runtime_seconds
                                ep_item.rating = ep_meta.rating
                                ep_item.provider_ids = dict(ep_meta.provider_ids)
                                ep_item.match_state = "matched"
                                ep_item.matched_at = now
                                ep_item.match_error = None
                                ep.air_date = ep_meta.air_date
                                if ep_meta.credits:
                                    await self._apply_credits(session, ep_item.id, ep_meta.credits)
                                if ep_meta.images:
                                    await self._store_images(
                                        ep_item.id, provider_name, ep_meta.images
                                    )
                            else:
                                ep_item.match_state = "unmatched"
                else:
                    if sea_item:
                        sea_item.match_state = "unmatched"
                    episodes = list(
                        await session.scalars(
                            select(Episode).where(Episode.season_id == season.item_id)
                        )
                    )
                    for ep in episodes:
                        ep_item = await session.get(MediaItem, ep.item_id)
                        if ep_item:
                            ep_item.match_state = "unmatched"
            unseasoned_date_eps = list(
                await session.scalars(
                    select(Episode).where(
                        Episode.series_id == snapshot.id,
                        Episode.season_id.is_(None),
                        Episode.air_date.is_not(None),
                    )
                )
            )
            if unseasoned_date_eps:
                years: set[int] = set()
                for ep in unseasoned_date_eps:
                    if ep.air_date and len(ep.air_date) >= 4 and ep.air_date[:4].isdigit():
                        years.add(int(ep.air_date[:4]))
                for year in sorted(years):
                    self._provider_calls += 1
                    try:
                        season_meta = await provider.fetch_tv_season(provider_id, year)
                    except ProviderError:
                        season_meta = None
                    if season_meta is not None:
                        sea_row = await session.scalar(
                            select(Season).where(
                                Season.series_id == snapshot.id,
                                Season.season_number == year,
                            )
                        )
                        if sea_row is None:
                            sea_id = new_id()
                            sea_title = season_meta.title or f"Season {year}"
                            sea_item = MediaItem(
                                id=sea_id,
                                library_id=item.library_id,
                                type="season",
                                title=sea_title,
                                sort_title=sort_title(sea_title),
                                overview=season_meta.overview,
                                added_at=now,
                                match_state="matched",
                                matched_at=now,
                            )
                            sea_item.season = Season(
                                series_id=snapshot.id,
                                season_number=year,
                            )
                            session.add(sea_item)
                            await session.flush()
                            sea_item_id = sea_id
                        else:
                            sea_item_id = sea_row.item_id

                        tmdb_by_date = {e.air_date: e for e in season_meta.episodes if e.air_date}
                        for ep in unseasoned_date_eps:
                            if ep.air_date and ep.air_date in tmdb_by_date:
                                ep_meta = tmdb_by_date[ep.air_date]
                                ep.season_id = sea_item_id
                                ep.season_number = year
                                ep.episode_number = ep_meta.episode_number
                                ep_item = await session.get(MediaItem, ep.item_id)
                                if ep_item is not None:
                                    ep_item.title = ep_meta.title
                                    ep_item.sort_title = sort_title(ep_meta.title)
                                    ep_item.overview = ep_meta.overview
                                    ep_item.runtime_seconds = ep_meta.runtime_seconds
                                    ep_item.rating = ep_meta.rating
                                    ep_item.provider_ids = dict(ep_meta.provider_ids)
                                    ep_item.match_state = "matched"
                                    ep_item.matched_at = now
                                    ep_item.match_error = None
                                    ep.air_date = ep_meta.air_date
                                    if ep_meta.credits:
                                        await self._apply_credits(
                                            session, ep_item.id, ep_meta.credits
                                        )
                                    if ep_meta.images:
                                        await self._store_images(
                                            ep_item.id, provider_name, ep_meta.images
                                        )

            await session.commit()

        images_stored = await self._store_images(snapshot.id, provider_name, images)
        return MatchResult(
            item_id=snapshot.id,
            state="matched",
            provider=provider_name,
            provider_id=provider_id,
            score=score,
            images=images_stored,
        )

    async def _match_artist(self, snapshot: _ItemSnapshot) -> MatchResult:
        # 1. Local image discovery (artist.jpg, artist.png, etc.)
        for path_str in snapshot.file_paths:
            p = Path(path_str)
            candidate_dirs = (p.parent, p.parent.parent)
            found_art: str | None = None
            for c_dir in candidate_dirs:
                found_art = find_artist_artwork(c_dir)
                if found_art:
                    break
            if found_art:
                await self._store_images(
                    snapshot.id,
                    "local",
                    [ProviderImage(kind="poster", file_path=found_art, is_primary=True)],
                )
                break

        # 2. Online enrichment via MusicBrainz (if available)
        mb = self._musicbrainz
        if mb is not None and mb.enabled:
            artist_meta = None
            self._provider_calls += 1
            if snapshot.musicbrainz_id:
                try:
                    artist_meta = await mb.fetch_artist_details(snapshot.musicbrainz_id)
                except (ProviderError, ProviderUnavailable) as exc:
                    return await self._save_failure(snapshot.id, str(exc))
            else:
                try:
                    candidates = await mb.search_artist(snapshot.title)
                    if candidates:
                        self._provider_calls += 1
                        artist_meta = await mb.fetch_artist_details(candidates[0].provider_id)
                except (ProviderError, ProviderUnavailable) as exc:
                    return await self._save_failure(snapshot.id, str(exc))

            if artist_meta is not None:
                now = self.clock()
                async with self.sessionmaker() as session:
                    item = await session.get(MediaItem, snapshot.id)
                    if item is not None and item.artist is not None:
                        item.artist.musicbrainz_id = artist_meta.provider_id
                        if artist_meta.sort_name:
                            item.artist.sort_name = artist_meta.sort_name
                        if artist_meta.country:
                            item.artist.country = artist_meta.country
                        if artist_meta.disambiguation:
                            item.artist.disambiguation = artist_meta.disambiguation
                        if artist_meta.overview:
                            item.overview = artist_meta.overview
                        item.match_state = "matched"
                        item.matched_at = now
                        item.next_match_at = None
                        provider_ids = dict(item.provider_ids or {})
                        provider_ids["musicbrainz"] = artist_meta.provider_id
                        item.provider_ids = provider_ids
                        await self._apply_genres(session, snapshot.id, artist_meta.genres)
                        await session.commit()
                return MatchResult(
                    item_id=snapshot.id,
                    state="matched",
                    provider=mb.name,
                    provider_id=artist_meta.provider_id,
                    score=1.0,
                )
            return await self._save_unmatched(snapshot.id, "No artist match on MusicBrainz")

        # MusicBrainz disabled or not present: mark matched
        now = self.clock()
        async with self.sessionmaker() as session:
            item = await session.get(MediaItem, snapshot.id)
            if item is not None:
                item.match_state = "matched"
                item.matched_at = now
                await session.commit()
        return MatchResult(item_id=snapshot.id, state="matched")

    async def _match_album(self, snapshot: _ItemSnapshot) -> MatchResult:
        mb = self._musicbrainz
        if mb is not None and mb.enabled:
            album_meta = None
            self._provider_calls += 1
            if snapshot.musicbrainz_id:
                try:
                    album_meta = await mb.fetch_album_details(snapshot.musicbrainz_id)
                except (ProviderError, ProviderUnavailable) as exc:
                    return await self._save_failure(snapshot.id, str(exc))
            else:
                try:
                    candidates = await mb.search_album(snapshot.title, artist=snapshot.artist_name)
                    if candidates:
                        self._provider_calls += 1
                        album_meta = await mb.fetch_album_details(candidates[0].provider_id)
                except (ProviderError, ProviderUnavailable) as exc:
                    return await self._save_failure(snapshot.id, str(exc))

            if album_meta is not None:
                now = self.clock()
                async with self.sessionmaker() as session:
                    item = await session.get(MediaItem, snapshot.id)
                    if item is not None and item.album is not None:
                        item.album.musicbrainz_id = album_meta.provider_id
                        if album_meta.release_group_id:
                            item.album.musicbrainz_release_group_id = album_meta.release_group_id
                        if album_meta.release_date:
                            item.album.release_date = album_meta.release_date
                        if album_meta.total_discs:
                            item.album.total_discs = album_meta.total_discs
                        if album_meta.total_tracks:
                            item.album.total_tracks = album_meta.total_tracks
                        if album_meta.year and not item.year:
                            item.year = album_meta.year
                        item.match_state = "matched"
                        item.matched_at = now
                        item.next_match_at = None
                        provider_ids = dict(item.provider_ids or {})
                        provider_ids["musicbrainz"] = album_meta.provider_id
                        if album_meta.release_group_id:
                            provider_ids["musicbrainz_release_group"] = album_meta.release_group_id
                        item.provider_ids = provider_ids
                        await self._apply_genres(session, snapshot.id, album_meta.genres)
                        await session.commit()

                # If album has no poster image, fetch Cover Art Archive image
                stored_images = 0
                if album_meta.images:
                    async with self.sessionmaker() as session:
                        has_img = await session.scalar(
                            select(ImageModel.id).where(ImageModel.item_id == snapshot.id).limit(1)
                        )
                    if not has_img:
                        stored_images = await self._store_images(
                            snapshot.id, mb.name, album_meta.images
                        )

                return MatchResult(
                    item_id=snapshot.id,
                    state="matched",
                    provider=mb.name,
                    provider_id=album_meta.provider_id,
                    score=1.0,
                    images=stored_images,
                )
            return await self._save_unmatched(snapshot.id, "No album match on MusicBrainz")

        # MusicBrainz disabled: mark matched
        now = self.clock()
        async with self.sessionmaker() as session:
            item = await session.get(MediaItem, snapshot.id)
            if item is not None:
                item.match_state = "matched"
                item.matched_at = now
                await session.commit()
        return MatchResult(item_id=snapshot.id, state="matched")

    async def _match_book(self, snapshot: _ItemSnapshot) -> MatchResult:
        """Match one book via local OPF, Open Library, or Google Books fallback."""
        now = self.clock()
        details: BookMetadata | None = None

        # 1. Local metadata check (always wins)
        for file_path in snapshot.file_paths:
            p = Path(file_path)
            local_opf = find_local_book_opf(p.parent)
            local_cover = find_local_book_cover(p.parent)
            if local_opf:
                details = parse_local_opf(local_opf, cover_path=local_cover)
                return await self._apply_book_match(
                    snapshot, "local", str(local_opf), details, list(details.images)
                )

        # 2. Open Library check
        if self._openlibrary and self._openlibrary.enabled:
            self._provider_calls += 1
            try:
                candidates = await self._openlibrary.search_book(
                    snapshot.title,
                    author=snapshot.author_name,
                    isbn=snapshot.isbn,
                )
                if candidates:
                    best = candidates[0]
                    self._provider_calls += 1
                    details = await self._openlibrary.fetch_book_details(best.provider_id)
                    if details is not None:
                        images = list(details.images)
                        if not images:
                            images = await self._openlibrary.fetch_book_images(best.provider_id)
                        return await self._apply_book_match(
                            snapshot, "openlibrary", best.provider_id, details, images
                        )
            except Exception as exc:
                logger.warning("Open Library match failed for %s: %s", snapshot.id, exc)

        # 3. Google Books fallback
        if self._googlebooks and self._googlebooks.enabled:
            self._provider_calls += 1
            try:
                candidates = await self._googlebooks.search_book(
                    snapshot.title,
                    author=snapshot.author_name,
                    isbn=snapshot.isbn,
                )
                if candidates:
                    best = candidates[0]
                    self._provider_calls += 1
                    details = await self._googlebooks.fetch_book_details(best.provider_id)
                    if details is not None:
                        images = list(details.images)
                        return await self._apply_book_match(
                            snapshot, "googlebooks", best.provider_id, details, images
                        )
            except Exception as exc:
                logger.warning("Google Books match failed for %s: %s", snapshot.id, exc)

        # No match found
        async with self.sessionmaker() as session:
            item = await session.get(MediaItem, snapshot.id)
            if item is not None:
                item.match_state = "unmatched"
                item.next_match_at = now + timedelta(
                    hours=self.settings.metadata_unmatched_retry_hours
                )
                await session.commit()
        return MatchResult(item_id=snapshot.id, state="unmatched")

    async def _apply_book_match(
        self,
        snapshot: _ItemSnapshot,
        provider_name: str,
        provider_id: str,
        details: BookMetadata,
        images: Sequence[ProviderImage],
    ) -> MatchResult:
        now = self.clock()
        stored_count = await self._store_images(snapshot.id, provider_name, images)
        async with self.sessionmaker() as session:
            item = await session.get(MediaItem, snapshot.id)
            if item is None:
                return MatchResult(item_id=snapshot.id, state="pending", error="Item deleted")

            item.match_state = "matched"
            item.matched_at = now
            item.match_error = None
            if details.overview and not item.overview:
                item.overview = details.overview
            if details.published_date and len(details.published_date) >= 4 and not item.year:
                with contextlib.suppress(ValueError):
                    item.year = int(details.published_date[:4])

            book = await session.get(Book, snapshot.id)
            if book is not None:
                if details.isbn and not book.isbn:
                    book.isbn = details.isbn
                if details.publisher and not book.publisher:
                    book.publisher = details.publisher
                if details.published_date and not book.published_date:
                    book.published_date = details.published_date
                if details.page_count and not book.page_count:
                    book.page_count = details.page_count
                if details.author_name and not book.author_id:
                    existing_author = await session.scalar(
                        select(Author).where(func.lower(Author.name) == details.author_name.lower())
                    )
                    if existing_author is None:
                        existing_author = Author(
                            name=details.author_name, sort_name=details.author_name
                        )
                        session.add(existing_author)
                        await session.flush()
                    book.author_id = existing_author.id

            await session.commit()
        return MatchResult(item_id=snapshot.id, state="matched", images=stored_count)

    async def _apply_genres(
        self, session: AsyncSession, item_id: str, names: Sequence[str]
    ) -> None:
        await session.execute(delete(ItemGenre).where(ItemGenre.item_id == item_id))
        if not names:
            return
        existing = {genre.name.casefold(): genre for genre in await session.scalars(select(Genre))}
        chosen: list[Genre] = []
        for raw_name in dict.fromkeys(names):
            name = raw_name.strip()
            if not name:
                continue
            genre = existing.get(name.casefold())
            if genre is None:
                genre = Genre(id=new_id(), name=name)
                session.add(genre)
                existing[name.casefold()] = genre
            chosen.append(genre)
        # Genres must exist before the association rows that reference them.
        await session.flush()
        for genre in chosen:
            session.add(ItemGenre(item_id=item_id, genre_id=genre.id))

    async def _apply_credits(
        self, session: AsyncSession, item_id: str, credits: Sequence[PersonCredit]
    ) -> None:
        await session.execute(delete(ItemPerson).where(ItemPerson.item_id == item_id))
        if not credits:
            return

        people = list(await session.scalars(select(Person)))
        by_tmdb = {person.tmdb_id: person for person in people if person.tmdb_id}
        by_name = {person.name.casefold(): person for person in people}
        resolved: list[tuple[Person, PersonCredit]] = []

        for credit in credits:
            if credit.role not in {"cast", "director", "writer"} or not credit.name:
                continue
            person = by_tmdb.get(credit.provider_id) if credit.provider_id else None
            if person is None:
                person = by_name.get(credit.name.casefold())
            if person is None:
                person = Person(id=new_id(), name=credit.name, tmdb_id=credit.provider_id or None)
                session.add(person)
                by_name[credit.name.casefold()] = person
                if credit.provider_id:
                    by_tmdb[credit.provider_id] = person
            resolved.append((person, credit))

        # People must exist before the credit rows that reference them.
        await session.flush()
        seen: set[tuple[str, str]] = set()
        for person, credit in resolved:
            key = (person.id, credit.role)
            if key in seen:
                continue
            seen.add(key)
            session.add(
                ItemPerson(
                    item_id=item_id,
                    person_id=person.id,
                    role=credit.role,
                    character=credit.character,
                    ordering=credit.ordering,
                )
            )

    async def _store_images(
        self, item_id: str, provider_name: str, images: Sequence[ProviderImage]
    ) -> int:
        if self.artwork is None or not images:
            return 0
        try:
            return await self.artwork(item_id, provider_name, list(images))
        except Exception:
            logger.warning(
                "image storage failed", extra={"item_id": item_id, "provider": provider_name}
            )
            return 0

    async def _save_failure(
        self, item_id: str, error: str, *, state: MatchState = "pending"
    ) -> MatchResult:
        now = self.clock()
        async with self.sessionmaker() as session:
            item = await session.get(MediaItem, item_id)
            if item is None:
                return MatchResult(item_id=item_id, state=state, error=error)
            if item.match_state == "matched":
                return MatchResult(item_id=item_id, state="matched")
            attempts = item.match_attempts + 1
            item.match_state = state
            item.match_attempts = attempts
            item.match_error = error[:2000]
            item.next_match_at = now + self._retry_delay(attempts, state)
            await session.commit()
        return MatchResult(item_id=item_id, state=state, error=error)

    async def _save_unmatched(self, item_id: str, error: str) -> MatchResult:
        return await self._save_failure(item_id, error, state="unmatched")

    def _retry_delay(self, attempts: int, state: MatchState) -> timedelta:
        if state == "unmatched":
            return timedelta(hours=self.settings.metadata_unmatched_retry_hours)
        hours = self.settings.metadata_retry_base_hours * (2 ** max(0, attempts - 1))
        return timedelta(hours=min(hours, self.settings.metadata_retry_max_hours))
