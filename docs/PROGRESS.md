# Hearth Implementation Progress

## Status Overview

- **Current Phase**: Phase 2: Media probing, scanner and movie metadata
- **Overall Status**: In progress

---

## Phase 0: Bootstrap and tooling

**Goal:** An empty but fully wired project that builds, lints, tests, and runs in Docker.

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 0.1 | Initialize repo with `uv`, `pyproject.toml`, Ruff, Mypy, Pytest, `.gitignore`, `.editorconfig`, pre-commit hooks | Complete | `chore: initialize repo, uv tooling, and project configurations`; hooks added in `ci: add lint, type, test, and container smoke workflow` |
| 0.2 | Create package layout with placeholder modules | Complete | `chore: create package layout and placeholder modules` |
| 0.3 | Implement `core/config.py`, `core/logging.py`, and unified error handler | Complete | `feat: add settings, JSON logging, and unified error handling` |
| 0.4 | Implement `app/main.py` with app factory, `/api/v1/health` and `/api/v1/version` | Complete | `feat: add app factory, health/version endpoints, and request IDs` |
| 0.5 | Implement Typer CLI (`api`, `worker`, `migrate`, `create-admin`) | Complete | `feat: add Typer CLI entrypoints` |
| 0.6 | Dockerfile (multi-stage, non-root, tini, VAAPI) and `compose.yaml` | Complete | `feat: add multi-stage Docker image and compose stack` |
| 0.7 | GitHub Actions CI workflow | Complete | `ci: add lint, type, test, and container smoke workflow` |
| 0.8 | Write `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, and finalize Phase 0 | Complete | `docs: add architecture overview and Phase 0 acceptance results` |

### Phase 0 Acceptance Checklist

- [x] `uv run pytest`, `ruff check`, `mypy app` all pass.
  - `pytest`: 38 passed.
  - `ruff check`: clean; `ruff format --check`: clean.
  - `mypy app`: no issues in 24 source files.
- [x] Container runs as non-root user and health endpoint reports ffmpeg version.
  - `id` inside the container: `uid=1000(hearth) gid=1000(hearth) groups=1000(hearth),105`.
  - Root filesystem read-only, all capabilities dropped, `no-new-privileges` set.
  - `/api/v1/health` reports `ffmpeg.version = 5.1.9-0+deb12u1`.
- [x] Inside the container, `vainfo` runs (or reports `vaapi: unavailable` on a non-GPU host).
  - On this GPU host, `vainfo` lists profiles (Intel iHD driver 23.1.1) and health reports `vaapi.status = ok`.
  - With `docker/compose.cpu.yaml` (no `/dev/dri`), health reports `vaapi.status = unavailable`
    with detail `render device not present`, and overall health stays `ok`.

### Deliverables

- Green local quality gates and CI workflow (`.github/workflows/ci.yml`) covering lint,
  formatting, strict typing, tests, wheel build and a container health smoke test.
- `docker compose up` serves `/api/v1/health` from the built image.
- Architecture and decision records live in `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`.

### Known Phase 0 Limitations (intentional)

- Database health is file-level only; SQLAlchemy, migrations and real DB status arrive in Phase 1.
- `hearth worker` idles and `hearth migrate` / `hearth create-admin` are stubs
  (`create-admin` exits non-zero so it cannot be mistaken for success).
- Only `/api/v1/health` and `/api/v1/version` are exposed; authentication, media
  libraries and all media features start in Phase 1.
- Media access tokens, HLS, and the deeper VAAPI capability probe are Phase 3/4 work.

---

## Phase 1: Foundation: database, users and auth

**Goal:** A secure multi-user API skeleton that later phases build on.

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 1.1 | Async SQLAlchemy base/session, SQLite pragmas, Alembic baseline, locked startup migrations | Complete | `feat: add async database foundation and locked migrations` |
| 1.2 | Models: `User`, `RefreshToken`, `Library`, `LibraryPath`, `LibraryAccess`, `Setting`, `Job`, `AuditLog` | Complete | `feat: add phase 1 domain models and migration` |
| 1.3 | Argon2 password hashing and password policy | Complete | `feat: add argon2 password hashing and credential policy` |
| 1.4 | Auth endpoints: setup, login, refresh rotation/reuse detection, logout, me | Complete | `feat: add setup, login, refresh, logout and me endpoints` |
| 1.5 | Short-lived JWT access tokens and hashed refresh tokens | Complete | `test: cover access-token claims and refresh-token hashing` |
| 1.6 | Media access tokens (HMAC, scoped, for query-string consumers) | Complete | `feat: add scoped HMAC media access tokens` |
| 1.7 | Roles and dependencies (`require_user`, `require_admin`, `require_library_access`) | Complete | `feat: add role and library-access dependencies` |
| 1.8 | Admin user management and 15-enabled-user limit | Complete | `feat: add admin user management and enabled-user limit` |
| 1.9 | Admin library CRUD with media-root path validation | Complete | `feat: add library CRUD with media-root containment` |
| 1.10 | Login rate limiting and admin audit log | Complete | `feat: add login throttling and admin audit access` |
| 1.11 | Jobs table, asyncio worker, retries, cancellation and SSE events | Complete | `feat: add durable job worker, job API and SSE events` |
| 1.12 | Generate and commit `docs/openapi.json` | Complete | `chore: export and verify the OpenAPI schema` |

### Phase 1 Acceptance Checklist

- [x] Setup endpoint returns 409 once an admin exists.
  - `tests/integration/test_auth.py::test_setup_is_rejected_once_an_admin_exists`
    asserts 409 / `setup_complete`.
- [x] A disabled user's tokens stop working immediately.
  - Access tokens are revalidated against `users.disabled` on every request
    (`test_disabled_user_token_stops_working_immediately`,
    `test_disabling_user_invalidates_tokens_immediately`). Disabling also bumps
    `token_version`, revokes refresh tokens, and re-enabling does not resurrect
    the old access token.
- [x] A user without library access receives 404 (not 403) for that library's items.
  - `require_library_access` returns `library_not_found` 404 for missing and
    ungranted libraries (`test_ungranted_library_returns_404_not_403`). Phase 2
    item routes must reuse this dependency; library detail is the Phase 1
    surface that proves the rule.
- [x] Reusing an already-rotated refresh token revokes the whole token family.
  - `test_refresh_rotates_and_reuse_revokes_the_family` replays the old token,
    then proves the newly rotated token is also dead and every row is revoked.
- [x] Job survives a worker restart (re-queued if it was running).
  - `test_running_job_is_requeued_after_restart` seeds a job claimed by a dead
    worker, starts a new worker, observes `pending`, then `succeeded` with the
    attempt count preserved.
- [x] OpenAPI schema is generated and committed to `docs/openapi.json` by a script.
  - `scripts/export_openapi.py` (with `--check`), enforced by
    `tests/unit/test_openapi.py` and the CI quality job.

### Phase 1 Quality Gates

- `uv run ruff check`: clean; `uv run ruff format --check`: clean.
- `uv run mypy app`: no issues in 46 source files.
- `uv run pytest -q`: 130 passed.
- `uv run python scripts/export_openapi.py --check`: up to date.
- 18 API paths documented in `docs/openapi.json`.

### Deliverables

- Working multi-user authentication: first-run setup, login, rotating refresh
  tokens with reuse detection, logout, `/me`, and media access tokens.
- Admin user management with options, library grants and the 15-enabled-user cap.
- Admin library CRUD with media-root containment checks.
- Durable job table, asyncio worker, no-op job handler, retries, cancellation,
  restart requeue and SSE progress.
- Admin audit log with a read endpoint.
- Login throttling per IP and per username.
- `docs/openapi.json` generated by a script and verified in CI.

### Known Phase 1 Limitations (intentional)

- The job API and SSE stream are admin-only; per-user job visibility arrives with
  later scan/metadata features.
- SSE requires the bearer header, so browsers must use `fetch`-based SSE rather
  than `EventSource`. A dedicated event token is deferred to the API hardening
  phase.
- Logout revokes refresh tokens immediately but leaves the current access token
  valid for its remaining lifetime (at most 15 minutes) so other devices are not
  logged out.
- Login throttling is process-local and resets on restart; that is acceptable for
  a single-node server.
- Item, playback and streaming routes do not exist until Phases 2–4. The
  library-access dependency they will use is already tested.
- Media access tokens are minted and verifiable but not yet consumed by a stream
  endpoint.

---

## Phase 2: Media probing, scanner and movie metadata

**Goal:** Movie libraries are discovered, probed, matched to TMDB and browsable through the API.

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 2.1 | Typed ffprobe wrapper with timeout and stored stream metadata | Complete | `feat: add typed ffprobe wrapper` |
| 2.2 | Catalog models (`MediaItem`, `Movie`, `MediaFile`, images, people, genres, provider cache, FTS5) | Complete | `feat: add media catalog models and FTS5 search` |
| 2.3 | Scanner with change detection, soft deletes and mass-deletion safeguard | Complete | `feat: add media scanner with change detection` |
| 2.4 | Movie filename parsing with `guessit` and folder hints | Complete | `feat: parse movie filenames with guessit` |
| 2.5 | Metadata provider interface, TMDB provider with cache, local NFO provider | Complete | `feat: add TMDB and local metadata providers` |
| 2.6 | Matching, manual match/search endpoints, scan integration | Complete | `feat: add metadata matching and manual match API` |
| 2.7 | Image download, resizing variants and BlurHash | Complete | `feat: add artwork image pipeline` |
| 2.8 | Scheduled scans, scan job handler and filesystem watcher | Pending | Pending |
| 2.9 | Movie browse/read API with cursor pagination and item detail | Pending | Pending |

### Phase 2 Acceptance Checklist

- [ ] Re-scanning an unchanged library performs zero ffprobe calls and zero provider calls.
- [ ] Renaming a file is detected as a move (partial hash) and preserves watch state.
- [ ] Provider failures never fail a scan (item stays pending and is retried later).
- [ ] A fixture library of 50 generated files scans in under 30 seconds on CI.
- [ ] Unit tests cover filename parsing edge cases.

---

## Open Questions

None currently.

## Hardware Verification Notes

- Host has `/dev/dri/renderD128` (video GID 44, render GID 105).
- Verified on 2026-10-21 with Docker 26.1.5 and Compose 2.26.1:
  - Container image built from `docker/Dockerfile` (`python:3.12-slim-bookworm`, uv 0.10.7).
  - `vainfo` inside the container with `--device /dev/dri/renderD128` and
    `--group-add 105` reports `VA-API version 1.17 (libva 2.12.0)` and
    `Intel iHD driver for Intel(R) Gen Graphics - 23.1.1`, listing H.264, HEVC,
    VP9, AV1 and other profiles.
  - `vainfo` also prints harmless `XDG_RUNTIME_DIR`/X server warnings on stderr;
    the probe succeeds because VA profiles are present.
  - CPU-only path (`docker/compose.cpu.yaml`) reports `vaapi: unavailable` and
    remains healthy.
- Deeper capability probing and real test encodes are deferred to Phase 4 and
  must be verified on this hardware then; no encode performance is claimed yet.
