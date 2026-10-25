# Hearth Architecture

Hearth is a single-node, self-hosted media server for movies, TV, music, and
books. It is API-first: the backend is independent of any frontend choice until
Phase 9.

## Runtime topology

```text
                       reverse proxy / LAN
                              │
                     ┌────────▼────────┐
                     │  hearth api     │  FastAPI + uvicorn
                     │  (UID 1000)     │
                     └───┬────────┬────┘
        read/write       │        │        read-only
       /config (SQLite)  │        │        /media
                         │        │
                     ┌───▼────┐   │   ┌────▼──────────┐
                     │ SQLite │   │   │ media library │
                     │  WAL   │   │   └───────────────┘
                     └────────┘   │
                                  │  /transcode (tmpfs)
                     ┌────────────▼────────────┐
                     │ ffmpeg (VAAPI or x264)  │
                     └─────────────────────────┘

                     ┌─────────────────────────┐
                     │ hearth worker           │  same image, asyncio
                     │ jobs table + handlers   │  polls SQLite
                     └─────────────────────────┘
```

The API and worker run from the same image with different commands
(`hearth api`, `hearth worker`). A single node with a handful of concurrent
streams is the design target; there is no horizontal scaling or Redis.

## Module layout

```text
app/
  main.py              FastAPI app factory, exception handlers, lifespan
  cli.py               Typer: api, worker, migrate, create-admin
  core/
    config.py          pydantic-settings, HEARTH_* environment
    logging.py         JSON logs, request-ID ContextVar
    middleware.py      request ID + access log
    errors.py          AppError hierarchy and unified handlers
    deps.py            auth, roles, library access, settings/session dependencies
    passwords.py       Argon2id hashing and credential policy
    secrets.py         persistent secret and purpose-derived keys
    tokens.py          access JWT, media HMAC token, refresh helpers
    ratelimit.py       login sliding-window limiter
    ids.py             UUIDv7 generator
    system.py          ffmpeg/VAAPI/database health probes
  api/
    v1/                health, auth, libraries, users, jobs, events, admin,
                       items, images, metadata
  db/
    base.py            declarative base, UTC datetime type, naming conventions
    models.py          users, refresh tokens, libraries, settings, jobs, audit
    session.py         async engine, SQLite pragmas, request sessions
    migrate.py         locked Alembic upgrade runner
    migrations/        Alembic environment and revisions
  services/
    audit.py           append-only admin audit entries
    libraries.py       media-root path validation
    scanner/           walkers, watchers, matchers (Phase 2)
    metadata/          TMDB, MusicBrainz, Open Library, NFO (Phase 2+)
    media/             ffprobe wrapper, tags, book readers (Phase 2+)
    transcode/         capabilities, decision engine, builder, sessions (Phase 7)
    images/            fetch, resize, cache, BlurHash (Phase 2)
    jobs/              handler registry, no-op job, durable worker
  workers/             reserved for future worker-only modules
tests/
  unit/ integration/ e2e/ fixtures/
docker/                Dockerfile, compose.yaml, compose.cpu.yaml
docs/                  PROGRESS.md, DECISIONS.md, ARCHITECTURE.md, openapi.json
scripts/               dev helpers, OpenAPI export
```

## Request lifecycle

1. Uvicorn receives an HTTP request.
2. `RequestIDMiddleware` validates or generates an `X-Request-ID`, stores it in a
   `ContextVar`, and starts a monotonic timer.
3. FastAPI resolves dependencies (`SettingsDep` reads the settings bound to
   `app.state`) and runs the route.
4. Domain code raises `AppError` subclasses for expected failures. Handlers
   serialize everything to `{"error": {"code", "message", "details"}}`:
   `AppError`, `RequestValidationError`, framework `HTTPException`, and a
   catch-all that logs a stack trace but never leaks it.
5. The middleware emits one structured JSON access log line (method, path,
   status, duration) and returns the request ID in the response header.

All logs go to stdout as single-line JSON; the container runtime collects them.

## Authentication and authorization

1. `POST /api/v1/auth/setup` creates the first administrator only while the
   `users` table is empty. Subsequent calls return 409.
2. `POST /api/v1/auth/login` verifies an Argon2id hash and returns a 15-minute
   HS256 access JWT plus an opaque refresh token. Refresh tokens are stored as
   SHA-256 hashes and rotate on every use; replaying a revoked token revokes the
   entire family.
3. Every authenticated request decodes the JWT and reloads the user, so
   `disabled` and `token_version` changes take effect immediately.
4. `POST /api/v1/auth/media-tokens` mints a separate HMAC token for `<video>`,
   `<img>` and `<audio>` URLs. It is scoped to a user (optionally an item), lasts
   six hours by default, and cannot authenticate management endpoints.
5. `require_user`, `require_admin` and `require_library_access` centralise role
   and library grants. Missing and ungranted libraries both return 404.
6. Login failures are throttled per IP and per username with a process-local
   sliding window (5 per 60 seconds by default).
7. Administrative actions append to `audit_log`; `GET /api/v1/admin/audit`
   exposes them to admins.

## Jobs and events

The `jobs` table is the queue. The worker claims one row with a conditional
`UPDATE ... RETURNING`, respecting per-type and global concurrency limits. On
startup any `running` row is returned to `pending`, which recovers work after a
crash. Handler failures retry with capped exponential backoff; cancellation is
cooperative through a callback. `GET /api/v1/events` streams job snapshots and
updates as SSE by polling the table, so it works across the API/worker process
boundary. Only administrators may enqueue, inspect, cancel or stream jobs.

## Catalog read API

`GET /api/v1/libraries/{id}/items` lists a library's movies with keyset
(cursor) pagination. The cursor is the base64url-encoded sort key plus item id;
it is validated against the requested `sort`/`order`, so a cursor cannot be
replayed under a different ordering. Sort keys are title, added date, year and
rating; null years and ratings are coalesced to a sentinel so `NULL`s order last
in both directions. Filters are genre (case-insensitive), year and watched
state, the last resolved per user through `user_item_state`.

`GET /api/v1/items/{id}` returns the detail shape (movie fields, credits,
versions, artwork and the caller's watch state). `GET /api/v1/images/{id}`
serves a resized WebP/JPEG/PNG variant of a cached original. Both enforce
library access and return 404 rather than 403 for items the caller may not see.

Image URLs cannot send an `Authorization` header, so the image route accepts
either a bearer token or a media token in the query string (`ViewerDep`). Media
tokens may be scoped to one item and are never accepted as bearer credentials,
so they cannot reach the JSON read or management endpoints.

## Configuration

`Settings` is loaded once per process from `HEARTH_*` variables (optionally a
`.env` file) and cached. The app factory accepts explicit settings so tests use
temporary paths. Defaults assume the container filesystem contract:
`/config` persistent, `/transcode` tmpfs, `/media` read-only, and
`/dev/dri/renderD128` optional.

## Security boundaries

- The container runs as UID/GID 1000 with a read-only root filesystem, all
  Linux capabilities dropped, `no-new-privileges`, and only `/config` writable.
- Media is mounted read-only and every served path is a registered
  `media_file` inside a library root; item and artwork routes return 404 unless
  the caller has a grant (or is an administrator).
- No shell string interpolation: subprocesses always receive argument lists
  (`asyncio.create_subprocess_exec`).
- Errors return stable codes without internal details; health checks are
  read-only and never create the database.
- Secrets belong in environment variables or `/config`, never the repository.
- Authentication, roles, media access tokens, login throttling, the durable job
  queue, scanning, metadata matching and the movie read API are implemented;
  streaming and transcoding arrive in Phases 3–4.

## Health contract

`GET /api/v1/health` returns:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "database": {"status": "ok", "detail": null},
  "ffmpeg": {"status": "ok", "detail": null, "version": "5.1.9-0+deb12u1"},
  "vaapi": {"status": "ok", "detail": null, "device": "/dev/dri/renderD128",
            "driver": "Intel iHD driver for Intel(R) Gen Graphics - 23.1.1 ()"}
}
```

`status` is `degraded` only when ffmpeg is unusable or an existing database is
unreadable. VAAPI is optional because software transcoding is supported. Startup
runs migrations, so a healthy server normally reports `database.status = ok`; a
missing database is still accepted as `not_initialized` because health checks
themselves never create or migrate it.

## Testing and CI

- Unit tests cover pure logic and the health/error contracts with temporary
  paths and stubbed probes.
- Integration tests use a temp SQLite database, generated fixture media and the
  real ffprobe where the claim under test is about probing (scanner, matching,
  read API). Acceptance tests for scan cost and duration live in
  `tests/integration/test_phase2_acceptance.py`.
- Hardware-dependent tests will be marked `@pytest.mark.vaapi`.
- CI runs ruff (lint and format), strict mypy, pytest and a wheel build on
  Python 3.12, then builds the image and asserts the CPU-only health contract
  and non-root identity.
- Hardware verification is manual on the real server; results are recorded in
  `docs/PROGRESS.md`.
