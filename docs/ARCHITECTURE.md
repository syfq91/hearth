# Hearth Architecture

Hearth is a single-node, self-hosted media server for movies, TV, music, and
books. It is API-first: the backend is independent of any frontend choice until
Phase 9, and third-party clients are supported through OpenSubsonic (music) and
OPDS (books).

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
                     │ jobs table + handlers   │  (Phase 1)
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
    deps.py            shared FastAPI dependencies
    system.py          ffmpeg/VAAPI/database health probes
  api/
    v1/                /api/v1 (health, version; feature routers later)
    subsonic/          /rest/* OpenSubsonic compatibility (Phase 6)
    opds/              /opds/* OPDS 1.2 compatibility (Phase 7)
  db/                  SQLAlchemy base, models, session, Alembic (Phase 1)
  services/
    scanner/           walkers, watchers, matchers (Phase 2)
    metadata/          TMDB, MusicBrainz, Open Library, NFO (Phase 2+)
    media/             ffprobe wrapper, tags, book readers (Phase 2+)
    transcode/         capabilities, decision engine, builder, sessions (Phase 4)
    images/            fetch, resize, cache, BlurHash (Phase 2)
    jobs/              queue, worker, handlers (Phase 1)
  workers/             worker entrypoint (Phase 1)
tests/
  unit/ integration/ e2e/ fixtures/
docker/                Dockerfile, compose.yaml, compose.cpu.yaml
docs/                  PROGRESS.md, DECISIONS.md, ARCHITECTURE.md
scripts/               dev helpers and fixture generators (Phase 2+)
```

Only files needed by Phase 0 exist so far. Placeholder packages are retained so
later phases do not need to invent layout.

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

## Configuration

`Settings` is loaded once per process from `HEARTH_*` variables (optionally a
`.env` file) and cached. The app factory accepts explicit settings so tests use
temporary paths. Defaults assume the container filesystem contract:
`/config` persistent, `/transcode` tmpfs, `/media` read-only, and
`/dev/dri/renderD128` optional.

## Security boundaries

- The container runs as UID/GID 1000 with a read-only root filesystem, all
  Linux capabilities dropped, `no-new-privileges`, and only `/config` writable.
- Media is mounted read-only and will be validated against registered library
  roots before any file is served (Phase 3).
- No shell string interpolation: subprocesses always receive argument lists
  (`asyncio.create_subprocess_exec`).
- Errors return stable codes without internal details; health checks are
  read-only and never create the database.
- Secrets belong in environment variables or `/config`, never the repository.
- Authentication, roles, media access tokens and rate limiting arrive in
  Phase 1. Until then the API exposes only `/api/v1/health` and
  `/api/v1/version`.

## Health contract

`GET /api/v1/health` returns:

```json
{
  "status": "ok",
  "version": "0.1.0",
  "database": {"status": "not_initialized", "detail": "..."},
  "ffmpeg": {"status": "ok", "detail": null, "version": "5.1.9-0+deb12u1"},
  "vaapi": {"status": "ok", "detail": null, "device": "/dev/dri/renderD128",
            "driver": "Intel iHD driver for Intel(R) Gen Graphics - 23.1.1 ()"}
}
```

`status` is `degraded` only when ffmpeg is unusable or an existing database is
unreadable. VAAPI is optional because software transcoding is supported. A
missing database is expected until Phase 1 and does not degrade health.

## Testing and CI

- Unit tests cover pure logic and the health/error contracts with temporary
  paths and stubbed probes.
- Integration tests (temp SQLite + fixture library) and e2e tests (compose up)
  arrive with the features they exercise.
- Hardware-dependent tests will be marked `@pytest.mark.vaapi`.
- CI runs ruff (lint and format), strict mypy, pytest and a wheel build on
  Python 3.12, then builds the image and asserts the CPU-only health contract
  and non-root identity.
- Hardware verification is manual on the real server; results are recorded in
  `docs/PROGRESS.md`.
