"""Route-parametrized authorization matrix over the full OpenAPI surface.

Phase 8 requires every route in ``docs/openapi.json`` to carry an explicit
authorization policy, enforced by a test that fails when a route has no
declared policy. The declared table below is cross-checked against the
dependency wiring actually registered on the application, and each operation
is probed over HTTP as anonymous, regular-user, and admin callers.
"""

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.main import create_app
from scripts.export_openapi import OUTPUT

HTTP_METHODS = ("get", "post", "put", "patch", "delete")

ADMIN = {"username": "admin", "password": "correct horse"}
USER = {"username": "viewer", "password": "correct horse"}

# Any non-empty string satisfies str path parameters; the resource will 404.
DUMMY_ID = "00000000-0000-4000-8000-000000000000"

# Server-Sent Events streams never terminate once authentication succeeds;
# probes must not await the response body for these operations.
SSE_OPERATIONS = {("GET", "/api/v1/events")}

# Declared authorization policy per OpenAPI operation:
#   public - no credentials required
#   auth   - any authenticated account (access token)
#   admin  - administrator account only
#   media  - viewer identity: access token or media token
POLICY: dict[tuple[str, str], str] = {
    ("GET", "/api/v1/about"): "public",
    ("GET", "/api/v1/admin/audit"): "admin",
    ("POST", "/api/v1/admin/backup"): "admin",
    ("POST", "/api/v1/admin/items/{item_id}/rematch"): "admin",
    ("GET", "/api/v1/admin/jobs"): "admin",
    ("POST", "/api/v1/admin/jobs/{job_id}/cancel"): "admin",
    ("POST", "/api/v1/admin/libraries/scan"): "admin",
    ("POST", "/api/v1/admin/libraries/{library_id}/scan"): "admin",
    ("GET", "/api/v1/admin/logs"): "admin",
    ("POST", "/api/v1/admin/restore"): "admin",
    ("GET", "/api/v1/admin/system/info"): "admin",
    ("GET", "/api/v1/admin/transcoding/capabilities"): "admin",
    ("GET", "/api/v1/admin/transcoding/sessions"): "admin",
    ("DELETE", "/api/v1/admin/transcoding/sessions/{session_id}"): "admin",
    ("GET", "/api/v1/admin/transcoding/sessions/{session_id}/logs"): "admin",
    ("POST", "/api/v1/admin/transcoding/sessions/{session_id}/stop"): "admin",
    ("GET", "/api/v1/admin/transcoding/settings"): "admin",
    ("PUT", "/api/v1/admin/transcoding/settings"): "admin",
    ("GET", "/api/v1/admin/unmatched"): "admin",
    ("GET", "/api/v1/albums"): "auth",
    ("GET", "/api/v1/albums/{album_id}"): "auth",
    ("GET", "/api/v1/artists"): "auth",
    ("GET", "/api/v1/artists/{artist_id}"): "auth",
    ("POST", "/api/v1/auth/login"): "public",
    ("POST", "/api/v1/auth/logout"): "auth",
    ("GET", "/api/v1/auth/me"): "auth",
    ("POST", "/api/v1/auth/media-tokens"): "auth",
    ("POST", "/api/v1/auth/refresh"): "public",
    ("POST", "/api/v1/auth/setup"): "public",
    ("GET", "/api/v1/authors"): "auth",
    ("GET", "/api/v1/authors/{author_id}"): "auth",
    ("GET", "/api/v1/book-series"): "auth",
    ("GET", "/api/v1/book-series/{series_id}"): "auth",
    ("GET", "/api/v1/books"): "auth",
    ("GET", "/api/v1/books/search"): "auth",
    ("GET", "/api/v1/books/{book_id}"): "auth",
    ("GET", "/api/v1/books/{book_id}/file/{format}"): "media",
    ("GET", "/api/v1/books/{book_id}/pages/{page_number}"): "media",
    ("GET", "/api/v1/books/{book_id}/progress"): "auth",
    ("PUT", "/api/v1/books/{book_id}/progress"): "auth",
    ("GET", "/api/v1/collections"): "auth",
    ("POST", "/api/v1/collections"): "admin",
    ("DELETE", "/api/v1/collections/{collection_id}"): "admin",
    ("GET", "/api/v1/collections/{collection_id}"): "auth",
    ("PATCH", "/api/v1/collections/{collection_id}"): "admin",
    ("POST", "/api/v1/collections/{collection_id}/items"): "admin",
    ("PUT", "/api/v1/collections/{collection_id}/items/order"): "admin",
    ("DELETE", "/api/v1/collections/{collection_id}/items/{item_id}"): "admin",
    ("GET", "/api/v1/episodes/{episode_id}"): "auth",
    ("GET", "/api/v1/events"): "auth",
    ("GET", "/api/v1/health"): "public",
    ("GET", "/api/v1/hls/{session_id}/init.mp4"): "media",
    ("GET", "/api/v1/hls/{session_id}/master.m3u8"): "media",
    ("GET", "/api/v1/hls/{session_id}/media.m3u8"): "media",
    ("POST", "/api/v1/hls/{session_id}/stop"): "media",
    ("GET", "/api/v1/hls/{session_id}/{segment_file}"): "media",
    ("GET", "/api/v1/home/continue-watching"): "auth",
    ("GET", "/api/v1/home/favorites"): "auth",
    ("GET", "/api/v1/home/recently-added"): "auth",
    ("GET", "/api/v1/images/{image_id}"): "media",
    ("GET", "/api/v1/items/{item_id}"): "auth",
    ("POST", "/api/v1/items/{item_id}/favorite"): "auth",
    ("POST", "/api/v1/items/{item_id}/match"): "admin",
    ("POST", "/api/v1/items/{item_id}/playback-info"): "auth",
    ("POST", "/api/v1/items/{item_id}/progress"): "auth",
    ("PUT", "/api/v1/items/{item_id}/rating"): "auth",
    ("POST", "/api/v1/items/{item_id}/watched"): "auth",
    ("GET", "/api/v1/jobs"): "admin",
    ("POST", "/api/v1/jobs"): "admin",
    ("GET", "/api/v1/jobs/{job_id}"): "admin",
    ("POST", "/api/v1/jobs/{job_id}/cancel"): "admin",
    ("GET", "/api/v1/libraries"): "auth",
    ("POST", "/api/v1/libraries"): "admin",
    ("DELETE", "/api/v1/libraries/{library_id}"): "admin",
    ("GET", "/api/v1/libraries/{library_id}"): "auth",
    ("PATCH", "/api/v1/libraries/{library_id}"): "admin",
    ("GET", "/api/v1/libraries/{library_id}/items"): "auth",
    ("POST", "/api/v1/libraries/{library_id}/scan"): "admin",
    ("GET", "/api/v1/metadata/search"): "admin",
    ("GET", "/api/v1/music/most-played"): "auth",
    ("GET", "/api/v1/music/random"): "auth",
    ("GET", "/api/v1/music/recently-added"): "auth",
    ("GET", "/api/v1/music/search"): "auth",
    ("GET", "/api/v1/playlists"): "auth",
    ("POST", "/api/v1/playlists"): "auth",
    ("DELETE", "/api/v1/playlists/{playlist_id}"): "auth",
    ("GET", "/api/v1/playlists/{playlist_id}"): "auth",
    ("PATCH", "/api/v1/playlists/{playlist_id}"): "auth",
    ("POST", "/api/v1/playlists/{playlist_id}/items"): "auth",
    ("PUT", "/api/v1/playlists/{playlist_id}/items/order"): "auth",
    ("PUT", "/api/v1/playlists/{playlist_id}/items/reorder"): "auth",
    ("DELETE", "/api/v1/playlists/{playlist_id}/items/{item_id}"): "auth",
    ("GET", "/api/v1/search"): "auth",
    ("GET", "/api/v1/seasons/{season_id}"): "auth",
    ("GET", "/api/v1/seasons/{season_id}/episodes"): "auth",
    ("POST", "/api/v1/seasons/{season_id}/watched"): "auth",
    ("GET", "/api/v1/series"): "auth",
    ("GET", "/api/v1/series/{series_id}"): "auth",
    ("GET", "/api/v1/series/{series_id}/next-up"): "auth",
    ("GET", "/api/v1/series/{series_id}/seasons"): "auth",
    ("POST", "/api/v1/series/{series_id}/watched"): "auth",
    ("GET", "/api/v1/shows/next-up"): "auth",
    ("GET", "/api/v1/stream/audio/{track_id}"): "media",
    ("GET", "/api/v1/stream/audio/{track_id}/transcode"): "media",
    ("GET", "/api/v1/stream/{file_id}"): "media",
    ("GET", "/api/v1/stream/{file_id}/transcode"): "media",
    ("GET", "/api/v1/subtitles/{subtitle_id}.vtt"): "media",
    ("GET", "/api/v1/tracks"): "auth",
    ("GET", "/api/v1/tracks/{track_id}"): "auth",
    ("GET", "/api/v1/users"): "admin",
    ("POST", "/api/v1/users"): "admin",
    ("DELETE", "/api/v1/users/me/listenbrainz"): "auth",
    ("GET", "/api/v1/users/me/listenbrainz"): "auth",
    ("PUT", "/api/v1/users/me/listenbrainz"): "auth",
    ("GET", "/api/v1/users/{user_id}"): "admin",
    ("PATCH", "/api/v1/users/{user_id}"): "admin",
    ("POST", "/api/v1/users/{user_id}/password"): "admin",
    ("GET", "/api/v1/version"): "public",
}

_SPEC = json.loads(OUTPUT.read_text(encoding="utf-8"))

OPERATIONS = sorted(
    (method.upper(), path)
    for path, item in _SPEC["paths"].items()
    for method in item
    if method in HTTP_METHODS
)


def _path_values(path_item: dict[str, Any], operation: dict[str, Any]) -> dict[str, str]:
    """Fill every path parameter with a syntactically valid dummy value."""
    parameters = list(path_item.get("parameters", [])) + list(operation.get("parameters", []))
    values: dict[str, str] = {}
    for parameter in parameters:
        if parameter.get("in") != "path":
            continue
        schema = parameter.get("schema", {})
        values[parameter["name"]] = "1" if schema.get("type") in ("integer", "number") else DUMMY_ID
    return values


def _url(method: str, path: str) -> str:
    """Concrete request URL for an OpenAPI operation template."""
    path_item = _SPEC["paths"][path]
    operation = path_item[method.lower()]
    return path.format(**_path_values(path_item, operation))


def _bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def _setup_admin(client: httpx.AsyncClient) -> dict[str, str]:
    res = await client.post("/api/v1/auth/setup", json=ADMIN)
    assert res.status_code == 201
    return res.json()


async def _login(client: httpx.AsyncClient, account: dict[str, str]) -> dict[str, str]:
    res = await client.post("/api/v1/auth/login", json=account)
    assert res.status_code == 200
    return res.json()


async def _create_user(client: httpx.AsyncClient, admin_headers: dict[str, str]) -> None:
    res = await client.post("/api/v1/users", json={**USER, "role": "user"}, headers=admin_headers)
    assert res.status_code == 201


async def _probe(
    client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str]
) -> httpx.Response:
    """Send an authorization probe, treating an open SSE stream as success."""
    if (method, url) in SSE_OPERATIONS:
        try:
            async with asyncio.timeout(2.0):
                return await client.request(method, url, headers=headers)
        except TimeoutError:
            # The stream opened and never terminated: authentication passed.
            return httpx.Response(200)
    return await client.request(method, url, headers=headers)


def _walk(routes: list[Any]) -> Any:
    """Yield API routes across FastAPI router-inclusion implementations."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _walk(inner.routes)
        elif hasattr(route, "dependant"):
            yield route
        elif hasattr(route, "routes") and not hasattr(route, "methods"):
            yield from _walk(route.routes)


def _policy_of_route(route: Any) -> str:
    """Derive the enforced policy from a route's dependency tree."""
    names: set[str] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dependency = stack.pop()
        names.add(getattr(dependency.call, "__name__", "") or "")
        stack.extend(dependency.dependencies)
    if "require_admin" in names:
        return "admin"
    if "get_viewer" in names:
        return "media"
    if names & {"require_user", "get_current_user", "get_events_user"}:
        return "auth"
    return "public"


def test_every_openapi_operation_has_a_declared_policy() -> None:
    """A route added without a policy entry fails here."""
    operations = set(OPERATIONS)
    declared = set(POLICY)
    missing = sorted(operations - declared)
    stale = sorted(declared - operations)
    assert not missing, f"OpenAPI operations without a declared policy: {missing}"
    assert not stale, f"policy entries without a matching OpenAPI operation: {stale}"


def test_declared_policy_matches_dependency_wiring(db_settings: Settings) -> None:
    """The declared table must match the dependencies each route is wired to."""
    application = create_app(db_settings)
    wired: dict[tuple[str, str], set[str]] = {}
    for route in _walk(application.routes):
        policy = _policy_of_route(route)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            wired.setdefault((method.upper(), "/api/v1" + route.path), set()).add(policy)

    mismatches = [
        f"{method} {path}: declared {POLICY.get((method, path))}, wired {sorted(policies)}"
        for (method, path), policies in sorted(wired.items())
        if policies != {POLICY.get((method, path))}
    ]
    unwired = sorted(set(POLICY) - set(wired))
    assert not mismatches, f"declared policy does not match route wiring: {mismatches}"
    assert not unwired, f"declared policies with no matching route: {unwired}"


@pytest.mark.parametrize(("method", "path"), OPERATIONS, ids=[f"{m} {p}" for m, p in OPERATIONS])
async def test_route_enforces_declared_policy(
    api_client: httpx.AsyncClient, method: str, path: str
) -> None:
    """Probe each operation as anonymous, regular-user, and admin callers."""
    policy = POLICY[(method, path)]
    url = _url(method, path)

    admin_tokens = await _setup_admin(api_client)
    admin_headers = _bearer(admin_tokens)
    await _create_user(api_client, admin_headers)
    user_headers = _bearer(await _login(api_client, USER))

    # 1. Anonymous callers.
    anonymous = await api_client.request(method, url)
    if policy == "public":
        assert anonymous.status_code not in (401, 403), (
            f"{method} {url}: public route rejected anonymous callers with "
            f"{anonymous.status_code}: {anonymous.text}"
        )
        return
    assert anonymous.status_code == 401, (
        f"{method} {url}: expected 401 without credentials, got "
        f"{anonymous.status_code}: {anonymous.text}"
    )

    # 2. Regular users (media routes may also authenticate with a media token).
    user = await _probe(api_client, method, url, user_headers)
    if policy == "admin":
        assert user.status_code == 403, (
            f"{method} {url}: expected 403 for a regular user, got {user.status_code}: {user.text}"
        )
    else:
        assert user.status_code not in (401, 403), (
            f"{method} {url}: authenticated user rejected with {user.status_code}: {user.text}"
        )

    if policy == "media":
        mint = await api_client.post("/api/v1/auth/media-tokens", json={}, headers=user_headers)
        assert mint.status_code == 201, mint.text
        via_media_token = await api_client.request(method, f"{url}?token={mint.json()['token']}")
        assert via_media_token.status_code not in (401, 403), (
            f"{method} {url}: media token rejected with "
            f"{via_media_token.status_code}: {via_media_token.text}"
        )

    # 3. Admin-only routes accept administrators.
    if policy == "admin":
        admin = await _probe(api_client, method, url, admin_headers)
        assert admin.status_code not in (401, 403), (
            f"{method} {url}: admin rejected with {admin.status_code}: {admin.text}"
        )
