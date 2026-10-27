"""Integration tests for Task 8.7 API hardening features.

Verifies:
- CORS middleware behavior (preflights, allowed origins, exposed headers)
- Request body size limiting (HTTP 413 payload_too_large)
- ETags and 304 Not Modified on GET /api/v1/items/{item_id}
- Query field selection on GET /api/v1/items/{item_id}?fields=...
"""

import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Library, MediaItem, Movie

ADMIN = {"username": "admin", "password": "correct horse"}


async def _setup(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert response.status_code == 201, response.text
    data: dict[str, str] = response.json()
    return data


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _seed_movie(
    sessionmaker: async_sessionmaker[AsyncSession], media_dir: Path
) -> tuple[str, str]:
    lib_path = media_dir / "movies"
    lib_path.mkdir(parents=True, exist_ok=True)
    async with sessionmaker() as session:
        library = Library(
            id=uuid.uuid4().hex,
            name="Movies",
            type="movie",
        )
        session.add(library)
        await session.flush()

        item = MediaItem(
            id=uuid.uuid4().hex,
            library_id=library.id,
            type="movie",
            title="Blade Runner 2049",
            sort_title="Blade Runner 2049",
            year=2017,
            overview="Thirty years after the events of the first film...",
            rating=8.0,
            content_rating="R",
        )
        session.add(item)
        await session.flush()

        movie = Movie(
            item_id=item.id,
            original_title="Blade Runner 2049",
            tagline="There's still a page left.",
        )
        session.add(movie)
        await session.commit()
        return library.id, item.id


@pytest.mark.asyncio
async def test_cors_preflight_and_headers(api_client: httpx.AsyncClient) -> None:
    """CORS middleware handles OPTIONS preflights and returns permissive headers."""
    preflight = await api_client.request(
        "OPTIONS",
        "/api/v1/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization, Content-Type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers.get("access-control-allow-origin") == "*"
    assert "GET" in preflight.headers.get("access-control-allow-methods", "")

    # GET response includes CORS headers
    resp = await api_client.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"


@pytest.mark.asyncio
async def test_request_size_limit_rejection(api_client: httpx.AsyncClient) -> None:
    """Requests with Content-Length exceeding max_request_body_bytes are rejected with 413."""
    tokens = await _setup(api_client)

    # An oversized request (e.g. 60 MiB)
    oversized_length = 60 * 1024 * 1024
    response = await api_client.post(
        "/api/v1/items/some-id/progress",
        headers={
            **_bearer(tokens),
            "Content-Length": str(oversized_length),
            "Content-Type": "application/json",
        },
        content=b"",
    )
    assert response.status_code == 413
    payload = response.json()
    assert payload["error"]["code"] == "payload_too_large"
    assert "exceeds maximum allowed size" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_item_detail_etags_and_conditional_caching(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    """GET /items/{id} returns an ETag and honors If-None-Match with 304."""
    tokens = await _setup(api_client)
    _lib_id, item_id = await _seed_movie(db_sessionmaker, db_settings.media_dir)

    # 1. Initial GET returns 200 and ETag
    resp1 = await api_client.get(f"/api/v1/items/{item_id}", headers=_bearer(tokens))
    assert resp1.status_code == 200
    etag1 = resp1.headers.get("etag")
    assert etag1 is not None
    assert resp1.json()["title"] == "Blade Runner 2049"

    # 2. Conditional GET with matching ETag returns 304 Not Modified
    resp2 = await api_client.get(
        f"/api/v1/items/{item_id}",
        headers={**_bearer(tokens), "If-None-Match": etag1},
    )
    assert resp2.status_code == 304
    assert resp2.headers.get("etag") == etag1
    assert resp2.content == b""

    # 3. Update watch state (favorite the item)
    fav_resp = await api_client.post(
        f"/api/v1/items/{item_id}/favorite",
        headers=_bearer(tokens),
        json={"favorite": True},
    )
    assert fav_resp.status_code == 200

    # 4. Old ETag now returns 200 with new ETag
    resp3 = await api_client.get(
        f"/api/v1/items/{item_id}",
        headers={**_bearer(tokens), "If-None-Match": etag1},
    )
    assert resp3.status_code == 200
    etag2 = resp3.headers.get("etag")
    assert etag2 is not None
    assert etag2 != etag1
    assert resp3.json()["watch_state"]["favorite"] is True


@pytest.mark.asyncio
async def test_item_detail_field_selection(
    api_client: httpx.AsyncClient,
    db_sessionmaker: async_sessionmaker[AsyncSession],
    db_settings: Settings,
) -> None:
    """GET /items/{id}?fields=... filters returned fields while preserving id and type."""
    tokens = await _setup(api_client)
    _lib_id, item_id = await _seed_movie(db_sessionmaker, db_settings.media_dir)

    resp = await api_client.get(
        f"/api/v1/items/{item_id}?fields=title,year,rating",
        headers=_bearer(tokens),
    )
    assert resp.status_code == 200
    data = resp.json()

    # Preserved identity fields
    assert data["id"] == item_id
    assert data["type"] == "movie"

    # Requested fields
    assert data["title"] == "Blade Runner 2049"
    assert data["year"] == 2017
    assert data["rating"] == 8.0

    # Unrequested fields omitted
    assert "overview" not in data
    assert "tagline" not in data
    assert "files" not in data
    assert "people" not in data
