"""MusicBrainz metadata provider with rate limiting, caching, and Cover Art Archive client."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.base import utcnow
from app.db.models import ProviderCache
from app.services.metadata.base import (
    AlbumMetadata,
    ArtistMetadata,
    MetadataProvider,
    MovieMetadata,
    ProviderError,
    ProviderImage,
    ProviderUnavailable,
    SearchResult,
)

logger = logging.getLogger("hearth.metadata.musicbrainz")


def _year_from_date(value: Any) -> int | None:
    if not isinstance(value, str) or len(value) < 4 or not value[:4].isdigit():
        return None
    return int(value[:4])


def _clean_str(val: Any) -> str | None:
    if not isinstance(val, str) or not val.strip():
        return None
    return val.strip()


class MusicBrainzProvider(MetadataProvider):
    """MusicBrainz metadata provider complying with strict 1 req/sec rate limiting."""

    name = "musicbrainz"

    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self._client = client
        self._owns_client = client is None
        self._last_request: float = 0.0
        self._rate_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.settings.musicbrainz_enabled

    @property
    def base_url(self) -> str:
        return self.settings.musicbrainz_base_url.rstrip("/")

    @property
    def caa_base_url(self) -> str:
        return self.settings.cover_art_archive_base_url.rstrip("/")

    # ------------------------------------------------------------- base stubs

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        return []

    async def fetch_details(self, provider_id: str) -> MovieMetadata | None:
        return None

    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        return []

    # ----------------------------------------------------------------- artist

    async def search_artist(self, query: str) -> list[SearchResult]:
        """Search MusicBrainz for artists by name."""
        clean = query.strip()
        if not clean:
            return []

        # Lucene search query
        escaped = clean.replace('"', '\\"')
        lucene_query = f'artist:"{escaped}"'
        cache_key = f"mb:artist_search:{clean.casefold()[:180]}"

        payload = await self._get_json(
            "/artist",
            {"query": lucene_query, "fmt": "json"},
            cache_key=cache_key,
        )

        results: list[SearchResult] = []
        for item in payload.get("artists") or []:
            if not isinstance(item, dict):
                continue
            mbid = item.get("id")
            name = item.get("name")
            if not mbid or not name:
                continue
            score = float(item.get("score") or 0)
            disambiguation = item.get("disambiguation")
            country = item.get("country")
            desc_parts = [p for p in (country, disambiguation) if p]
            results.append(
                SearchResult(
                    provider=self.name,
                    provider_id=str(mbid),
                    title=str(name),
                    overview=" - ".join(desc_parts) if desc_parts else None,
                    popularity=score,
                )
            )
        return results

    async def fetch_artist_details(self, artist_id: str) -> ArtistMetadata | None:
        """Fetch artist details by MusicBrainz ID."""
        if not artist_id:
            return None

        cache_key = f"mb:artist:{artist_id}"
        payload = await self._get_json(
            f"/artist/{artist_id}",
            {"inc": "genres+annotation", "fmt": "json"},
            cache_key=cache_key,
        )
        if not payload:
            return None

        name = payload.get("name")
        if not name:
            return None

        # Genres: from 'genres' or 'tags'
        genres_list: list[str] = []
        for g in payload.get("genres") or []:
            if isinstance(g, dict) and g.get("name"):
                genres_list.append(str(g["name"]).title())
        if not genres_list:
            for t in payload.get("tags") or []:
                if isinstance(t, dict) and t.get("name"):
                    genres_list.append(str(t["name"]).title())

        # Overview from annotation or disambiguation
        overview: str | None = None
        if payload.get("annotation") and isinstance(payload["annotation"], dict):
            overview = _clean_str(payload["annotation"].get("text"))
        if not overview:
            overview = _clean_str(payload.get("disambiguation"))

        return ArtistMetadata(
            provider=self.name,
            provider_id=str(artist_id),
            name=str(name),
            sort_name=_clean_str(payload.get("sort-name")),
            disambiguation=_clean_str(payload.get("disambiguation")),
            country=_clean_str(payload.get("country")),
            artist_type=_clean_str(payload.get("type")),
            genres=tuple(dict.fromkeys(genres_list)),
            overview=overview,
            raw=payload,
        )

    # ------------------------------------------------------------------ album

    async def search_album(self, query: str, artist: str | None = None) -> list[SearchResult]:
        """Search MusicBrainz for releases by title and optional artist."""
        clean_title = query.strip()
        if not clean_title:
            return []

        clean_artist = artist.strip() if artist else None
        escaped_title = clean_title.replace('"', '\\"')

        if clean_artist:
            escaped_artist = clean_artist.replace('"', '\\"')
            lucene_query = f'release:"{escaped_title}" AND artist:"{escaped_artist}"'
            a_sub = clean_artist.casefold()[:80]
            t_sub = clean_title.casefold()[:100]
            cache_key = f"mb:release_search:{a_sub}:{t_sub}"
        else:
            lucene_query = f'release:"{escaped_title}"'
            cache_key = f"mb:release_search:{clean_title.casefold()[:180]}"

        payload = await self._get_json(
            "/release",
            {"query": lucene_query, "fmt": "json"},
            cache_key=cache_key,
        )

        results: list[SearchResult] = []
        for item in payload.get("releases") or []:
            if not isinstance(item, dict):
                continue
            mbid = item.get("id")
            title = item.get("title")
            if not mbid or not title:
                continue
            score = float(item.get("score") or 0)
            date_str = item.get("date")
            year = _year_from_date(date_str)
            # Cover art archive front cover
            poster_url = f"{self.caa_base_url}/release/{mbid}/front"
            results.append(
                SearchResult(
                    provider=self.name,
                    provider_id=str(mbid),
                    title=str(title),
                    year=year,
                    poster_url=poster_url,
                    popularity=score,
                )
            )
        return results

    async def fetch_album_details(self, album_id: str) -> AlbumMetadata | None:
        """Fetch release details by release MBID."""
        if not album_id:
            return None

        cache_key = f"mb:release:{album_id}"
        payload = await self._get_json(
            f"/release/{album_id}",
            {"inc": "artists+labels+recordings+release-groups+genres", "fmt": "json"},
            cache_key=cache_key,
        )
        if not payload:
            return None

        title = payload.get("title")
        if not title:
            return None

        # Artist credit
        artist_name: str | None = None
        artist_mbid: str | None = None
        credits = payload.get("artist-credit") or []
        if credits and isinstance(credits[0], dict):
            first = credits[0]
            artist_name = first.get("name")
            if isinstance(first.get("artist"), dict):
                artist_mbid = first["artist"].get("id")

        # Media disc / track counts
        media_list = payload.get("media") or []
        total_discs = len(media_list) if media_list else 1
        total_tracks: int | None = sum(
            int(m.get("track-count") or 0) for m in media_list if isinstance(m, dict)
        )
        if total_tracks == 0:
            total_tracks = None

        # Release date / year
        date_str = _clean_str(payload.get("date"))
        year = _year_from_date(date_str)

        # Label
        label_name: str | None = None
        label_info = payload.get("label-info") or []
        if label_info and isinstance(label_info[0], dict):
            label_obj = label_info[0].get("label")
            if isinstance(label_obj, dict):
                label_name = _clean_str(label_obj.get("name"))

        # Release group MBID
        rg_id: str | None = None
        rg = payload.get("release-group")
        if isinstance(rg, dict):
            rg_id = rg.get("id")

        # Genres
        genres_list: list[str] = []
        for g in payload.get("genres") or []:
            if isinstance(g, dict) and g.get("name"):
                genres_list.append(str(g["name"]).title())
        if rg and isinstance(rg, dict):
            for g in rg.get("genres") or []:
                if isinstance(g, dict) and g.get("name"):
                    genres_list.append(str(g["name"]).title())

        # Cover Art Archive image
        poster_url = f"{self.caa_base_url}/release/{album_id}/front"
        images = (
            ProviderImage(
                kind="poster",
                source_url=poster_url,
                is_primary=True,
            ),
        )

        return AlbumMetadata(
            provider=self.name,
            provider_id=str(album_id),
            title=str(title),
            artist_name=artist_name,
            artist_provider_id=artist_mbid,
            release_date=date_str,
            year=year,
            total_discs=total_discs,
            total_tracks=total_tracks,
            genres=tuple(dict.fromkeys(genres_list)),
            label=label_name,
            barcode=_clean_str(payload.get("barcode")),
            release_group_id=rg_id,
            images=images,
            raw=payload,
        )

    async def fetch_album_images(self, album_id: str) -> list[ProviderImage]:
        """Fetch Cover Art Archive front cover for a release."""
        if not album_id:
            return []
        poster_url = f"{self.caa_base_url}/release/{album_id}/front"
        return [
            ProviderImage(
                kind="poster",
                source_url=poster_url,
                is_primary=True,
            )
        ]

    # -------------------------------------------------------------- internals

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.settings.musicbrainz_timeout_seconds,
            headers={
                "User-Agent": self.settings.musicbrainz_user_agent,
                "Accept": "application/json",
            },
        )

    async def _throttle(self) -> None:
        """Enforce strict 1 req/sec pacing for MusicBrainz API."""
        interval = self.settings.musicbrainz_rate_limit_seconds
        async with self._rate_lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            elapsed = now - self._last_request
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)
            self._last_request = loop.time()

    async def _cache_get(self, key: str) -> dict[str, Any] | None:
        async with self.sessionmaker() as session:
            entry = await session.get(ProviderCache, (self.name, key))
            if entry is None or entry.expires_at <= utcnow():
                return None
            return entry.payload if isinstance(entry.payload, dict) else None

    async def _cache_set(self, key: str, payload: dict[str, Any]) -> None:
        now = utcnow()
        async with self.sessionmaker() as session:
            await session.merge(
                ProviderCache(
                    provider=self.name,
                    key=key,
                    payload=payload,
                    fetched_at=now,
                    expires_at=now + timedelta(hours=self.settings.musicbrainz_cache_ttl_hours),
                )
            )
            await session.commit()

    async def _get_json(
        self,
        path: str,
        params: dict[str, str],
        *,
        cache_key: str,
    ) -> dict[str, Any]:
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        if not self.enabled:
            raise ProviderUnavailable("MusicBrainz provider is disabled")

        client = self._client or self._build_client()
        await self._throttle()
        headers = {
            "User-Agent": self.settings.musicbrainz_user_agent,
            "Accept": "application/json",
        }

        try:
            response = await client.get(path, params=params, headers=headers)
            if response.status_code == 429:
                # Back off on 429
                retry_after = 2.0
                await asyncio.sleep(retry_after)
                await self._throttle()
                response = await client.get(path, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"MusicBrainz request failed ({type(exc).__name__})") from None

        if response.status_code == 404:
            return {}

        if response.status_code >= 400:
            raise ProviderError(f"MusicBrainz returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except Exception as exc:
            raise ProviderError("MusicBrainz returned invalid JSON") from exc

        if isinstance(payload, dict):
            await self._cache_set(cache_key, payload)
            return payload
        return {}

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
