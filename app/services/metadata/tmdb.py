"""TMDB metadata provider with SQLite caching and request pacing."""

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
    MetadataProvider,
    MovieMetadata,
    PersonCredit,
    ProviderError,
    ProviderImage,
    ProviderUnavailable,
    SearchResult,
)

logger = logging.getLogger("hearth.metadata.tmdb")


def _year_from_date(value: Any) -> int | None:
    if not isinstance(value, str) or len(value) < 4 or not value[:4].isdigit():
        return None
    return int(value[:4])


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class TMDBProvider(MetadataProvider):
    """A small, cached client for the TMDB v3 API.

    The API key is sent either as a v4 bearer token (when it contains dots) or
    as the v3 ``api_key`` query parameter. It is never logged or included in an
    exception message.
    """

    name = "tmdb"

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
        self._last_request = 0.0
        self._rate_lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.tmdb_api_key)

    async def search(self, query: str, year: int | None = None) -> list[SearchResult]:
        params: dict[str, str | int] = {
            "query": query,
            "include_adult": "false",
            "language": "en-US",
        }
        if year is not None:
            params["year"] = year
        payload = await self._get_json(
            "/search/movie",
            params,
            cache_key=f"search:{year or ''}:{query.casefold()[:180]}",
        )

        results: list[SearchResult] = []
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            provider_id = item.get("id")
            title = item.get("title") or item.get("original_title")
            if provider_id is None or not title:
                continue
            results.append(
                SearchResult(
                    provider=self.name,
                    provider_id=str(provider_id),
                    title=str(title),
                    year=_year_from_date(item.get("release_date")),
                    overview=item.get("overview"),
                    poster_url=self._image_url(item.get("poster_path"), "w342"),
                    backdrop_url=self._image_url(item.get("backdrop_path"), "w780"),
                    popularity=_as_float(item.get("popularity")) or 0.0,
                )
            )
        return results

    async def fetch_details(self, provider_id: str) -> MovieMetadata | None:
        payload = await self._get_json(
            f"/movie/{provider_id}",
            {"language": "en-US", "append_to_response": "credits"},
            cache_key=f"movie:{provider_id}",
        )
        if not payload:
            return None

        title = payload.get("title") or payload.get("original_title")
        if not title:
            return None

        genres = tuple(
            str(genre["name"])
            for genre in payload.get("genres") or []
            if isinstance(genre, dict) and genre.get("name")
        )
        credits: list[PersonCredit] = []
        credits_payload = payload.get("credits") or {}
        for ordering, cast in enumerate(credits_payload.get("cast") or []):
            if not isinstance(cast, dict) or not cast.get("name"):
                continue
            credits.append(
                PersonCredit(
                    name=str(cast["name"]),
                    role="cast",
                    character=cast.get("character") or None,
                    ordering=ordering,
                    provider_id=str(cast["id"]) if cast.get("id") is not None else None,
                )
            )
        for crew in credits_payload.get("crew") or []:
            if not isinstance(crew, dict) or not crew.get("name"):
                continue
            job = (crew.get("job") or "").casefold()
            if job in {"director", "writer", "screenplay", "story"}:
                role = "director" if job == "director" else "writer"
                credits.append(
                    PersonCredit(
                        name=str(crew["name"]),
                        role=role,
                        ordering=len(credits),
                        provider_id=str(crew["id"]) if crew.get("id") is not None else None,
                    )
                )

        provider_ids = {"tmdb": str(payload.get("id") or provider_id)}
        if payload.get("imdb_id"):
            provider_ids["imdb"] = str(payload["imdb_id"])

        runtime_minutes = _as_int(payload.get("runtime"))
        rating = _as_float(payload.get("vote_average"))
        if rating is not None and rating <= 0:
            rating = None
        release_date = payload.get("release_date") or None

        return MovieMetadata(
            provider=self.name,
            provider_id=str(payload.get("id") or provider_id),
            title=str(title),
            year=_year_from_date(release_date),
            original_title=payload.get("original_title") or None,
            release_date=release_date,
            overview=payload.get("overview") or None,
            tagline=payload.get("tagline") or None,
            runtime_seconds=runtime_minutes * 60 if runtime_minutes else None,
            rating=rating,
            genres=genres,
            credits=tuple(credits),
            provider_ids=provider_ids,
            raw={"tmdb": payload},
        )

    async def fetch_images(self, provider_id: str) -> list[ProviderImage]:
        payload = await self._get_json(
            f"/movie/{provider_id}/images",
            {"include_image_language": "en,null"},
            cache_key=f"images:{provider_id}",
        )

        images: list[ProviderImage] = []
        for kind, key, size, limit in (
            ("poster", "posters", "w780", 4),
            ("backdrop", "backdrops", "w1280", 4),
        ):
            items = [
                item
                for item in payload.get(key) or []
                if isinstance(item, dict) and item.get("file_path")
            ]
            items.sort(
                key=lambda item: (
                    _as_float(item.get("vote_average")) or 0.0,
                    _as_int(item.get("vote_count")) or 0,
                ),
                reverse=True,
            )
            for index, item in enumerate(items[:limit]):
                images.append(
                    ProviderImage(
                        kind=kind,
                        source_url=self._image_url(item.get("file_path"), size),
                        width=_as_int(item.get("width")),
                        height=_as_int(item.get("height")),
                        is_primary=index == 0,
                        language=item.get("iso_639_1"),
                    )
                )
        return images

    async def _get_json(
        self,
        path: str,
        params: dict[str, str | int],
        *,
        cache_key: str,
    ) -> dict[str, Any]:
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        api_key = self.settings.tmdb_api_key
        if not api_key:
            raise ProviderUnavailable("TMDB API key is not configured")

        request_params = dict(params)
        headers: dict[str, str] = {}
        if api_key.count(".") == 2:
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            request_params["api_key"] = api_key

        payload = await self._request(path, request_params, headers)
        await self._cache_set(cache_key, payload)
        return payload

    async def _request(
        self,
        path: str,
        params: dict[str, str | int],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        client = self._client or self._build_client()
        await self._throttle()

        try:
            response = await client.get(path, params=params, headers=headers)
            if response.status_code == 429:
                retry_after = _as_float(response.headers.get("Retry-After")) or 1.0
                await asyncio.sleep(min(max(retry_after, 0.0), 5.0))
                await self._throttle()
                response = await client.get(path, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"TMDB request failed ({type(exc).__name__})") from None

        if response.status_code in {401, 403}:
            raise ProviderUnavailable("TMDB rejected the configured API key")
        if response.status_code == 404:
            raise ProviderError("TMDB resource not found")
        if response.status_code == 429:
            raise ProviderError("TMDB rate limit exceeded")
        if response.status_code >= 500:
            raise ProviderError(f"TMDB server error ({response.status_code})")
        if response.status_code >= 400:
            raise ProviderError(f"TMDB request failed ({response.status_code})")

        try:
            payload = response.json()
        except ValueError:
            raise ProviderError("TMDB returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise ProviderError("TMDB returned an unexpected response")
        return payload

    def _build_client(self) -> httpx.AsyncClient:
        self._client = httpx.AsyncClient(
            base_url=self.settings.tmdb_base_url.rstrip("/"),
            timeout=self.settings.tmdb_timeout_seconds,
        )
        return self._client

    async def _throttle(self) -> None:
        async with self._rate_lock:
            interval = self.settings.tmdb_min_interval_seconds
            if interval > 0:
                elapsed = asyncio.get_running_loop().time() - self._last_request
                if elapsed < interval:
                    await asyncio.sleep(interval - elapsed)
            self._last_request = asyncio.get_running_loop().time()

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
                    expires_at=now + timedelta(hours=self.settings.tmdb_cache_ttl_hours),
                )
            )
            await session.commit()

    def _image_url(self, path: Any, size: str) -> str | None:
        if not isinstance(path, str) or not path:
            return None
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.settings.tmdb_image_base_url.rstrip('/')}/{size}{path}"

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
