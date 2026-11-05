# AGENTS.md

Hearth is a single-node, self-hosted media server for movies, TV, music, books,
and comics. It ships as one Docker image and runs a FastAPI API plus a
background worker against the same SQLite database. This file is the
agent-facing guide. `README.md` is for end users; `docs/` holds operator guides,
the API contract, and architecture decisions.

Read these before making non-trivial changes:

- `docs/ARCHITECTURE.md` — runtime topology, module layout, request lifecycle.
- `docs/DEVELOPMENT.md` — the canonical local setup and command list.
- `docs/API.md` — HTTP conventions, auth, playback contract.
- `docs/DECISIONS.md` — ADRs; check before re-litigating a design choice.
- `docs/PROGRESS.md` — phase history, not a current TODO list.

## Stack

| Area | Technology |
| --- | --- |
| Backend | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2 async, SQLite (WAL), Alembic |
| CLI/worker | Typer (`hearth api`, `worker`, `migrate`, `backup`, …) |
| Media | ffmpeg/ffprobe, VA-API, HLS fMP4, direct-play byte ranges |
| Frontend | SvelteKit 3 + Svelte 5 runes, TypeScript strict, Vite, static SPA |
| Tests | pytest (asyncio auto), httpx ASGI client, Playwright |
| Tooling | uv, ruff, strict mypy, npm, Docker Compose |

The design target is one node, a handful of concurrent streams, and no external
infrastructure services (Redis, Postgres, message brokers). Metadata and
scrobbling providers are outbound HTTP integrations inside the app. Do not add
a separate frontend server or horizontal-scaling assumptions.

## Prerequisites

- Python 3.12+ and uv 0.10+ (CI pins Python 3.12 and uv 0.10.7).
- Node.js 22 and npm (web client and generated TypeScript client).
- `ffmpeg` and `ffprobe` on `PATH` for integration tests.
- Optional: `/dev/dri/renderD128` for `vaapi`-marked tests.

## Backend setup

```bash
uv sync

export HEARTH_DATA_DIR=./.dev-data
export HEARTH_DB_PATH=./.dev-data/hearth.db
export HEARTH_MEDIA_DIR=./media
export HEARTH_TRANSCODE_DIR=./.dev-transcode
mkdir -p "$HEARTH_DATA_DIR" "$HEARTH_MEDIA_DIR" "$HEARTH_TRANSCODE_DIR"

uv run hearth api --reload          # http://localhost:8000, OpenAPI at /docs
uv run hearth worker                # second terminal; scans/jobs only complete here
```

`HEARTH_EMBED_WORKER` is `false` by default outside Compose. The API only
enqueues jobs; without a worker, scans appear stuck. Compose sets it to `true`.

Migrations run automatically at API/worker startup behind a file lock. Useful
commands:

```bash
uv run hearth migrate               # apply migrations and exit
uv run hearth api --embed-worker    # single process, like the container
uv run hearth backup                # atomic SQLite backup
```

## Web client

The SPA is built into `web/dist` and served by FastAPI from `/`. There is no dev
proxy; the supported local flow is to build and let the API serve it:

```bash
cd web
npm ci
npm run check                       # svelte-check + TypeScript
npm run build                       # writes web/dist (gitignored)
cd ..
uv run hearth api --reload
```

The client calls same-origin `/api/v1`. `web/dist` is not committed; never edit
it or commit it.

## Commands

Run all commands from the repository root unless noted.

| Task | Command |
| --- | --- |
| Install backend | `uv sync` |
| Run API | `uv run hearth api --reload` |
| Run worker | `uv run hearth worker` |
| Full test suite | `uv run pytest` |
| Fast test subset | `uv run pytest tests/unit` |
| One test file / test | `uv run pytest tests/integration/test_home.py` / `... -k name` |
| Lint | `uv run ruff check` |
| Check formatting | `uv run ruff format --check` |
| Fix formatting | `uv run ruff format` |
| Type check | `uv run mypy app` |
| OpenAPI drift check | `uv run python scripts/export_openapi.py --check` |
| Generate OpenAPI | `uv run python scripts/export_openapi.py` |
| Client type drift check | `uv run python scripts/generate_client.py --check` |
| Generate client types | `uv run python scripts/generate_client.py` |
| Web type check | `cd web && npm run check` |
| Web build | `cd web && npm run build` |
| Playwright (Docker) | `./scripts/e2e.sh` |
| Playwright, one spec | `./scripts/e2e.sh video` |
| Reuse built e2e image | `SKIP_BUILD=1 ./scripts/e2e.sh` |
| Build image | `docker build -f docker/Dockerfile -t hearth:dev .` |
| Run with Compose | `docker compose --env-file .env -f docker/compose.yaml up -d` |
| Local hooks | `uvx pre-commit install` (then `uvx pre-commit run --all-files`) |

`.github/workflows/ci.yml` is the source of truth. Its jobs are `quality`
(ruff, mypy, OpenAPI check, pytest, wheel build), `web` (type check + static
build), `container` (image smoke/health/non-root/backup-restore), and `e2e`
(Playwright against a fresh container).

## Before you finish

1. `uv run ruff format` (or verify with `--check`), then `uv run ruff check`.
2. `uv run mypy app`.
3. `uv run pytest` (at minimum the affected tests; full suite before a PR).
4. If any route, request model, or response model changed:
   `uv run python scripts/export_openapi.py` and
   `uv run python scripts/generate_client.py`. Commit **both**
   `docs/openapi.json` and `packages/client/src/schema.d.ts`.
5. If the web app changed: `cd web && npm run check && npm run build`.
6. If you changed a user-visible behavior or contract, update the relevant
   docs (`docs/API.md`, `docs/CONFIGURATION.md`, `CHANGELOG.md`, ADRs).

A change is not complete while a CI check it affects is red.

## Repository map

```text
app/
  main.py             FastAPI factory, middleware, exception handlers, lifespan
  cli.py              Typer commands
  api/v1/              one module per resource; register in api/v1/__init__.py
  core/               settings, deps, auth, errors, logging, middleware, ids,
                      secrets, tokens, rate limiting, metrics, SPA static serving
  db/                 declarative base, models, async session, Alembic runner
    migrations/       Alembic env and numbered revisions
  services/           scanner, metadata, media, images, transcode, jobs,
                      playback state, subtitles, streaming, scrobbling, backup
  workers/            reserved for worker-only modules
tests/
  unit/               pure logic and contract tests with stubs
  integration/        real ASGI app, temp SQLite, generated media fixtures
  security/           hardening/security regression tests
  e2e/                container-level backup/restore tests (opt-in)
web/                  SvelteKit SPA; e2e specs in web/e2e
packages/client/      generated OpenAPI TypeScript definitions
scripts/              OpenAPI/client generation, e2e runner, load test
docker/               Dockerfile and Compose
docs/                 operator guides, API reference, ADRs
tools/testplayer/     standalone developer player served at /testplayer/
```

## Backend conventions

### API modules

- Each module in `app/api/v1/` owns an `APIRouter` with a prefix and tags, then
  is imported and included in `app/api/v1/__init__.py`.
- Define request/response Pydantic models next to the routes. Use
  `ConfigDict(from_attributes=True)` when serializing ORM objects.
- Inject settings, sessions, and identity through the `Annotated` dependencies
  in `app/core/deps.py` (`SettingsDep`, `SessionDep`, `RequireUser`,
  `RequireAdmin`, `LibraryAccessDep`, `ViewerDep`, …). Do not read global
  settings or construct engines inside routes.
- Raise `AppError` subclasses (`NotFoundError`, `ValidationError`,
  `ConflictError`, `ForbiddenError`, …). The handlers serialize every error to
  `{"error": {"code", "message", "details"}}`; never return raw exception text
  or stack traces. Error codes are stable snake_case identifiers.
- For resources the caller cannot see, return **404**, not 403. Use
  `require_library_access`/`LibraryAccessDep` for library grants and
  `RequireAdmin` for management endpoints.
- Use `logger` with structured `extra={...}`. Do not `print`.
- Streaming endpoints must enforce access and validate every filesystem path
  against registered media files; media elements authenticate with scoped
  media tokens (`ViewerDep`) because they cannot send an `Authorization`
  header.

### Database

- SQLAlchemy 2.0 typed style: `Mapped[...]`, `mapped_column(...)`.
- IDs are UUIDv7 strings from `app.core.ids.new_id` (36 chars).
- Timestamps are timezone-aware UTC, stored with the custom `UTCDateTime` type;
  use `utcnow()` from `app.db.base`. Never use naive `datetime.now()`.
- SQLite allows one writer. Keep transactions short, never hold one across
  network/ffmpeg I/O, and rely on the configured WAL/busy-timeout pragmas.
- Schema changes need an Alembic revision in `app/db/migrations/versions/`:
  follow the `000N_snake_description.py` naming, set `down_revision` to the
  current head, and use `op.batch_alter_table` for SQLite table alterations.
  Migrations are applied at startup and in tests, so they must be safe to run
  on an existing database.
- Metadata providers implement the interface in
  `app/services/metadata/base.py`. Local files are preferred; online providers
  are fallbacks. Wire shared construction through
  `app/services/metadata/factory.py`.

### Jobs

- The `jobs` table is the queue; `hearth worker` claims rows. Do not add a
  separate broker.
- Register a new job type in `REGISTRY` in `app/services/jobs/handlers.py` with
  a `JobSpec` handler and per-type concurrency. Handler payloads must be
  JSON-serializable. Report progress and check cancellation through
  `JobContext`; raise `JobCancelled` cooperatively.
- Failures retry with capped exponential backoff; the worker recovers
  `running` rows after a crash.

### Configuration

- Add `HEARTH_*` settings as fields on `Settings` in `app/core/config.py`.
  The prefix is applied automatically.
- Document operator-facing variables in `docs/CONFIGURATION.md`, and keep
  `.env.example`/Compose examples consistent where relevant.
- Settings are cached per process. Tests pass an explicit `Settings` to
  `create_app`; do not call `get_settings()` in test paths that need isolation.

### Subprocesses and I/O

- Always use `asyncio.create_subprocess_exec` with an argument list. Never use a
  shell or string interpolation (see the security boundaries in
  `docs/ARCHITECTURE.md`).
- Use `asyncio.to_thread` for blocking filesystem or CPU work.
- Resolve and validate paths before opening files; reject traversal and paths
  outside the registered library root.

## Frontend conventions

- Svelte 5 runes only: `$state`, `$derived`, `$effect`, `$props`. Runes mode is
  forced for project files by `web/vite.config.ts`. Follow the existing
  `.svelte.ts` state modules (`auth.svelte.ts`, `player.svelte.ts`,
  `media-token.svelte.ts`, `tv.svelte.ts`).
- Routes live under `web/src/routes`; shared components under
  `web/src/lib/components`; shared logic under `web/src/lib`. `+layout.ts` sets
  `ssr = false`, bootstraps auth, and redirects anonymous users to `/login`.
  Use `+page.ts` load functions for page data and the `PageProps`/`PageLoad`
  generated types.
- Call the API through `web/src/lib/api/client.ts` (`api.get/post/...`). It
  handles same-origin `/api/v1`, bearer auth, single-flight refresh on 401, the
  unified error envelope (`ApiError`), and media tokens. Do not hand-roll
  `fetch` calls or parse errors differently.
- Import API types type-only from `web/src/lib/api/types.ts` (`S['Name']`).
  `packages/client/src/schema.d.ts` is generated; never edit it by hand.
- The SPA is static (`adapter-static`, fallback `index.html`) and is served by
  FastAPI. Avoid SSR-only APIs.
- Build target is ES2019 for older TV browsers. Keep bundle-heavy libraries
  (`video.js`, readers) out of the initial route bundle where possible.
- TV mode (`web/src/lib/tv.svelte.ts`, `web/src/lib/actions/spatial-focus.ts`)
  must keep D-pad/arrow navigation and visible focus. Preserve keyboard and
  screen-reader affordances when touching the player or shelves.
- TypeScript is strict; use tabs and single quotes, matching existing files.

## Testing

- `pytest` runs with `asyncio_mode = "auto"`; async test functions need no
  decorator.
- Shared fixtures in `tests/conftest.py` provide isolated `Settings`
  (`db_settings`), a migrated session factory (`db_sessionmaker`), a session
  (`db_session`), and an ASGI `api_client` with lifespan. They use `tmp_path`
  and never touch the real data directory.
- Unit tests cover pure logic and contracts with stubs. Integration tests seed
  the database directly, exercise the real app, and use real ffprobe/ffmpeg
  where probing is the behavior under test.
- Tests must not depend on network access or production providers. Use the
  fakes in `tests/support.py` or provider-specific fakes.
- Mark hardware tests `vaapi`; container E2E tests are `container_e2e` and opt
  in with `HEARTH_CONTAINER_E2E=1`.
- Add or update a regression test for every bug fix and every new contract.
- Playwright specs share one server and one database, are serialized
  (`workers: 1`), and seed through the API. Fixtures live in
  `web/e2e/fixtures/media` and are mounted at `/media`. `scripts/e2e.sh` runs
  separate API and worker containers because scans only complete in the worker.

## Security invariants

- The container runs as UID/GID 1000 with a read-only root filesystem, dropped
  capabilities, `no-new-privileges`, read-only `/media`, and writable `/config`
  only. Do not weaken these defaults.
- Secrets never go in the repository, logs, or API responses. The app persists
  or derives keys; tests pass an explicit `secret_key`.
- Media tokens are scoped, short-lived, and accepted only as media credentials,
  never as bearer tokens for JSON/management endpoints.
- CORS comes from `HEARTH_CORS_ORIGINS` (the code default is `["*"]` for LAN
  use; restrict it when exposing Hearth publicly). Security headers and the
  request-body limit are global middleware.
- Health checks are read-only and must never create or migrate the database.
- Every served path must resolve to a registered media file inside a library
  root. Treat client-supplied paths as untrusted.

## Docs, decisions, and changelog

- User-facing overview stays in `README.md`; developer and operator detail goes
  in `docs/`.
- Any change to HTTP behavior updates `docs/API.md`; any new/changed setting
  updates `docs/CONFIGURATION.md`.
- Significant design or security decisions get an ADR appended to
  `docs/DECISIONS.md` using the existing format: title, Date, Status, Context,
  Decision, Alternatives Considered, with the next sequential number.
- Record notable changes in `CHANGELOG.md` (Keep a Changelog, SemVer).
- `docs/FRONTEND_DECISION.md` records the SvelteKit choice; do not reopen it
  without an explicit product decision.

## Git and pull requests

- Use Conventional Commits as the history does: `feat(web): ...`,
  `fix(books): ...`, `perf(web): ...`, `docs: ...`, `chore(openapi): ...`,
  `ci: ...`, `refactor(...)`, `style: ...`. Scope by area.
- Keep commits focused. A route/schema change and its regenerated
  `docs/openapi.json` + `packages/client/src/schema.d.ts` belong in the same
  commit (`chore(openapi): regenerate ...` is also acceptable when isolated).
- Before a PR: run the full local check list above, confirm generated
  artifacts are current, and describe the user-visible change plus the tests
  run. CI must be green.

## Common pitfalls

- **Scans never finish:** you started the API without a worker or
  `--embed-worker`.
- **Stale UI:** you edited Svelte sources but did not run `npm run build`; the
  API serves the old `web/dist`.
- **OpenAPI/client drift:** new or changed models without regenerating both
  artifacts fail CI.
- **Settings leakage:** `get_settings()` is cached; tests and factories should
  receive explicit `Settings`.
- **Transcode test isolation:** the session manager is a process singleton and
  is reset by the autouse fixture; do not bypass it.
- **SQLite lock errors:** long-held transactions or concurrent writes; keep
  transactions short and let WAL/busy-timeout work.
- **403 instead of 404:** leaking the existence of libraries/items the caller
  cannot access. Use 404.
- **Naive datetimes:** SQLite drops timezone info unless `UTCDateTime` is used.
- **`web/dist` edits:** it is generated and gitignored.
