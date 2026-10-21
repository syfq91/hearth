# Architecture Decision Records (ADRs)

## ADR 001: Package and Environment Management with uv

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Fast, reproducible, and lockfile-backed dependency management is required for both local development and multi-stage container builds.
- **Decision:** Use Astral's `uv` with `pyproject.toml` and PEP 735 `[dependency-groups]` for development dependencies.
- **Alternatives Considered:** Poetry (slower resolver/installs, less standard PEP 735 support), pip + venv (lacks native lockfile and fast caching).

## ADR 002: Render Group GID Configuration for Container

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Access to `/dev/dri/renderD128` inside unprivileged Docker containers requires membership in the host's `render` group, but the render GID varies across Linux distributions (e.g., 105 on Ubuntu/Debian, 992 on Arch, etc.).
- **Decision:** Parameterize `RENDER_GID` in `compose.yaml` with a default of `105` and document host GID detection (`getent group render`).
- **Alternatives Considered:** Running container as root (violates security-by-default rule), chmod 666 on `/dev/dri` on host (insecure host modification).

## ADR 003: Observable Core Primitives

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Every request must be traceable and every error must be machine-readable before any feature endpoints exist. The plan fixes the error envelope and requires JSON logs with a request ID.
- **Decision:** Emit single-line JSON to stdout via a custom `logging.Formatter`, and carry the request ID in a `ContextVar` so it propagates through async tasks without passing it explicitly. Errors are raised as `AppError` subclasses and serialized centrally to `{"error": {"code", "message", "details"}}`; FastAPI request-validation errors are mapped to the same shape with `jsonable_encoder` for safe serialization, and uncaught exceptions are logged with a stack trace but returned without internal details.
- **Alternatives Considered:** `structlog`/`python-json-logger` (extra dependency for a small formatter), per-endpoint error responses (inconsistent shapes), returning exception text to clients (leaks internals).

## ADR 004: Health Endpoint Semantics in Phase 0

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** The Phase 0 acceptance criteria require the health endpoint to report the ffmpeg version and honest VAAPI availability, but the database schema does not exist until Phase 1.
- **Decision:** `/api/v1/health` reports `database`, `ffmpeg` and `vaapi` components. Overall `status` is `ok` when ffmpeg works and the database is either `ok` or `not_initialized`; a missing database is expected until Phase 1 and is not degraded. VAAPI is informational only and never degrades health, because software transcoding is a supported fallback. Health checks open an existing database read-only and never create one.
- **Alternatives Considered:** Failing health when the DB is absent (breaks Phase 0 container checks), treating VAAPI as required (fails on every non-GPU host), probing capabilities deeply here (duplicates the Phase 4 probe and slows every health check).

## ADR 005: Request ID Trust and Propagation

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Logs need one correlation ID per request, and clients/proxies often supply `X-Request-ID`.
- **Decision:** Accept an incoming `X-Request-ID` only when it matches `[A-Za-z0-9._-]{1,64}`; otherwise generate a UUID4 hex value. Store it in a `ContextVar` for the lifetime of the request and echo it in the response header. This bounds untrusted header content before it reaches logs.
- **Alternatives Considered:** Always generating server-side IDs (loses proxy correlation), trusting arbitrary headers (log injection and unbounded values), storing the ID on `request.state` only (does not reach log records emitted by libraries).

## ADR 006: CLI Stub Behavior

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Phase 0 delivers the CLI contract before the database and job runner exist.
- **Decision:** `hearth api` runs uvicorn with our JSON logging and disables uvicorn's duplicate access log. `hearth worker` idles on a signal-driven asyncio loop so compose orchestration and shutdown handling can be verified now. `hearth migrate` logs its pending status and exits 0 (safe, idempotent stub), while `hearth create-admin` exits 1 because reporting success for an uncreated admin would be misleading.
- **Alternatives Considered:** Failing every stub (breaks compose startup before Phase 1), silently exiting 0 for `create-admin` (hides an unimplemented security-critical path).

## ADR 007: Container Image and Runtime Hardening

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** The image must carry ffmpeg, the Intel non-free VAAPI driver and the AMD/Mesa driver on a Debian base, while running unprivileged. The plan fixes Debian 12, non-root, tini and a parameterized render GID.
- **Decision:** Use a two-stage build on `python:3.12-slim-bookworm`: uv 0.10.7 installs the locked environment into `/app/.venv` (non-editable), then the runtime stage copies only the venv and adds ffmpeg, `vainfo`, `libva-drm2`, `mesa-va-drivers`, `intel-media-va-driver-non-free` and tini. Enable Debian's `non-free`/`non-free-firmware` components explicitly. Run as UID/GID 1000 `hearth` with a read-only root filesystem, all capabilities dropped, `no-new-privileges`, a writable `/config` volume, and tmpfs for `/transcode` (4 GB) and `/tmp`. Media is a read-only bind mount. The image healthcheck calls `/api/v1/health` with the stdlib so curl is not installed. GPU passthrough lives in the base compose file; `docker/compose.cpu.yaml` removes `devices` and `group_add` with Compose `!override` for hosts without a render node.
- **Alternatives Considered:** Alpine (musl driver gaps called out in the plan), running as root or privileged (rejected by the security rule), installing `curl` just for the healthcheck, hard-coding the render GID (varies by host), making GPU passthrough mandatory (breaks CPU-only hosts and the graceful-degradation criterion).

## ADR 008: CI Gates and Local Hooks

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** CI must prove lint, strict typing, tests and a runnable image, and the plan asks for pre-commit hooks.
- **Decision:** GitHub Actions runs a `quality` job (ruff check, ruff format check, mypy, pytest, wheel build) on Python 3.12 with uv 0.10.7 and `--locked` installs. A dependent `container` job builds the Dockerfile, starts it without a GPU, asserts the health contract (ffmpeg present, VAAPI unavailable, DB not initialized) and checks UID/GID 1000. Local hooks use ruff 0.17.1 plus basic hygiene hooks via `uvx pre-commit`; `check-yaml` excludes `compose.cpu.yaml` because Compose's `!override` tag is not plain YAML.
- **Alternatives Considered:** Running pytest inside the container (slower and duplicates the smoke test), using `prek` (viable, but pre-commit keeps contributor setup standard), making the container job independently buildable without tests (delays feedback and doubles runner cost).

## ADR 009: Async SQLite Foundation, UUIDv7 and Migration Locking

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Phase 1 needs a time-sortable identifier scheme, SQLite durability and concurrency pragmas, and schema creation that is safe when the API and worker start at the same time. Python 3.12 has no `uuid.uuid7`.
- **Decision:** Implement UUIDv7 directly (48-bit millisecond timestamp, RFC 9562 version/variant bits, random tail) and store IDs as 36-character text. Use SQLAlchemy 2.0 async with `aiosqlite`; every connection applies `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000` and `synchronous=NORMAL`. Use a custom `UTCDateTime` type because SQLite drops timezone information. Alembic runs from an async `env.py`, and `run_migrations_async` executes it in a worker thread because Alembic owns an event loop. Startup upgrades are serialised with `fcntl.flock` on `/config/.migrations.lock`, and the database URL is passed through Alembic `config.attributes` so tests never fall back to process-wide settings. The initial `0001_initial` revision is an empty baseline; Phase 1 tables arrive in the next revision.
- **Alternatives Considered:** `uuid6`/`uuid-utils` dependency (unnecessary for a small, well-specified function), naive `DateTime` columns with manual timezone handling (error-prone), running Alembic in-process (raises "asyncio.run() cannot be called from a running event loop"), SQLite `BEGIN IMMEDIATE` for migrations (does not cover pre-connection schema work), a separate migration container (compose complexity without a second writer).

## ADR 010: Phase 1 Identity and Library Schema

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Auth, per-user library grants and durable jobs need a schema that stays correct under SQLite while remaining Postgres-compatible.
- **Decision:** Usernames are normalized to lowercase and constrained by application validation, so the unique index is portable and login is case-insensitive. Access tokens carry a `token_version` claim; disabling a user is checked against the database on every request (immediate revocation) and password resets increment the version to invalidate outstanding access tokens. Refresh tokens store only a SHA-256 hash plus a `family_id`; rotation revokes the presented row and reuse of a revoked row revokes the family. Libraries own `library_paths` and `library_access` with cascade deletes and uniqueness per pair. `settings` is a JSON-encoded key/value table. Jobs carry claim, retry, cancellation and progress fields with a composite `(status, run_after)` index for the polling claim query. Audit rows are append-only and keep a nullable actor so deleting a user does not erase history.
- **Alternatives Considered:** Case-sensitive usernames with a lower() expression index (less portable), JWT-only revocation (cannot satisfy immediate disable), plaintext refresh tokens (credential leak), separate audit database (overkill for a single node), one table per job type (does not support a generic worker).

## ADR 011: Argon2id Parameters and Credential Input Bounds

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Password storage and login input need safe defaults without a tuning exercise.
- **Decision:** Use Argon2id at 19 MiB memory, 2 iterations and 1 lane (OWASP baseline), with per-hash random salts and `check_needs_rehash` support. Verification treats malformed hashes as a failed login. Passwords have a documented minimum (8, configurable) and a hard maximum of 1024 characters to bound hashing cost. Usernames are trimmed, lowercased and restricted to `[a-z0-9][a-z0-9_.-]{2,63}`.
- **Alternatives Considered:** bcrypt (memory-hardness and 72-byte truncation), PBKDF2 (weaker GPU resistance), unbounded password length (cheap denial of service against the hasher).

## ADR 012: Authentication Flow and Token Semantics

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Phase 1 needs a first-run admin, short-lived access tokens that can be revoked immediately, and refresh rotation resistant to replay.
- **Decision:** Setup is allowed only while `users` is empty and returns a token pair on success. Login normalizes usernames and verifies against a real dummy Argon2 hash when the user is unknown to equalize timing. Access tokens are HS256 JWTs (15 minutes) carrying `sub`, `role`, `tv` (token version), `type`, `iss`, `iat`, `exp` and `jti`; every authenticated request reloads the user so `disabled` and `token_version` take effect immediately. Refresh tokens are 48-byte URL-safe random strings stored only as SHA-256 hashes, share a `family_id` across rotations, and rotate on every use. A revoked token presented again revokes every still-live token in its family and returns 401 `refresh_token_reuse`. Rotation uses a conditional `UPDATE ... WHERE revoked_at IS NULL` so concurrent refreshes cannot both win. Logout revokes the presented token's family but leaves the access token valid until expiry (15 minutes) to avoid logging out other devices.
- **Alternatives Considered:** Storing refresh tokens in plaintext (leak risk), refresh reuse returning a generic 401 without family revocation (does not meet the phase criterion), incrementing `token_version` on logout (logs out all devices), server-side access-token sessions (more state than a home server needs), opaque access tokens (extra lookup on every request).
