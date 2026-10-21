# Hearth Implementation Progress

## Status Overview

- **Current Phase**: Phase 1 in progress (task 1.9 complete)
- **Overall Status**: Phase 0 accepted; Phase 1 underway

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
| 1.10 | Login rate limiting and admin audit log | Not started | |
| 1.11 | Jobs table, asyncio worker, retries, cancellation and SSE events | Not started | |
| 1.12 | Generate and commit `docs/openapi.json` | Not started | |

### Phase 1 Acceptance Checklist

- [ ] Setup endpoint returns 409 once an admin exists.
- [ ] A disabled user's tokens stop working immediately.
- [ ] A user without library access receives 404 (not 403) for that library's items.
- [ ] Reusing an already-rotated refresh token revokes the whole token family.
- [ ] Job survives a worker restart (re-queued if it was running).
- [ ] OpenAPI schema is generated and committed to `docs/openapi.json` by a script.

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
