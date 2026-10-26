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

## ADR 013: Token Validation Details

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Access tokens and refresh tokens need tightly defined validation so a token minted for one purpose cannot be replayed as another.
- **Decision:** Access tokens are validated for signature, issuer, audience (`hearth-api`), expiry, required claims and `type=access`. Token timestamps are truncated to whole seconds so the returned `expires_at` matches the JWT `exp` exactly. Refresh tokens are never stored raw; tests assert the database contains only 64-character SHA-256 digests.
- **Alternatives Considered:** Relying on signature alone (allows cross-purpose replay), accepting tokens without an audience (harder to separate future token types), returning microsecond-precision expiry that does not match the encoded claim.

## ADR 014: Media Access Token Format

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** `<video>`, `<img>` and `<audio>` cannot send an Authorization header, so media URLs need a separate, narrowly scoped credential. This is needed before Phase 3 streaming exists so the contract is stable.
- **Decision:** Tokens are `base64url(payload).base64url(HMAC-SHA256(media_key, payload))`, where the media key is derived from the installation secret with a separate domain string from the JWT key. The payload carries `sub`, optional `item`, `tv`, `scope=media`, `iat`, `exp` and `jti`. Default lifetime is 6 hours (`HEARTH_MEDIA_TOKEN_SECONDS`). Verification is constant-time, checks scope and expiry, and a dependency re-checks the user's disabled flag and token version on every media request. Minting requires a normal bearer token at `POST /api/v1/auth/media-tokens`. Media tokens cannot authenticate management endpoints because they are never accepted as bearer credentials and their scope is checked.
- **Alternatives Considered:** Signed URLs with JWT (larger and shares the access-token validation path), cookies (not usable by every native media element/client), long-lived API keys (cannot be revoked or scoped per item), putting access tokens in query strings (leaks a management credential into logs and history).

## ADR 015: Library Authorization Returns 404 and Admins Bypass Grants

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** The phase requires a user without library access to receive 404 rather than 403, and every later item/stream route must apply the same rule.
- **Decision:** `require_library_access(library_id)` loads the library, returns 404 `library_not_found` for both missing libraries and libraries the user has no grant for, and returns the ORM object otherwise. Administrators implicitly access every library; normal users require a `library_access` row. `GET /api/v1/libraries` filters rather than 404s because it is a collection. Later item and stream endpoints must depend on this helper (or an item-level equivalent) instead of checking access inline.
- **Alternatives Considered:** 403 for ungranted libraries (reveals existence and violates the criterion), admin-only implicit access disabled (makes administration require self-grants), per-route inline checks (easy to forget and hard to audit).

## ADR 016: User Management Safeguards

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Admin user management can lock a household out or silently leave old credentials valid.
- **Decision:** Admin-only `/api/v1/users` supports create, list, read, patch (username, role, enabled, bitrate cap, transcode permission, max rating, library grants) and password reset. Grants are replaced atomically on update and unknown library ids return 404. Creating or re-enabling an account checks `HEARTH_MAX_ENABLED_USERS` (default 15). Disabling bumps `token_version` and revokes all live refresh tokens, so re-enabling cannot resurrect a previously issued access token. Password reset bumps `token_version` and revokes refresh tokens. Admins cannot disable or demote themselves, and an update that would remove the final enabled admin is rejected. Audit entries record actions and changed field names but never passwords or hashes.
- **Alternatives Considered:** Soft deleting accounts (unnecessary now), letting admins disable themselves (easy lockout), leaving refresh tokens alive after reset/disable (violates immediate-revocation intent), deleting grants through separate endpoints (chatty and hard to keep atomic).

## ADR 017: Library Path Containment and Visibility

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Library roots come from administrators but must never allow a scan or stream to escape the read-only media mount. Normal users also do not need host filesystem layout.
- **Decision:** `validate_library_paths` requires absolute paths, resolves them with `strict=True`, requires a directory, checks it is inside `HEARTH_MEDIA_DIR` using `Path.is_relative_to` after resolution, and checks `R_OK|X_OK`. This blocks `..` segments and symlinks that point outside the mount. Duplicate paths are collapsed; an empty list is invalid. Admin CRUD lives on `/api/v1/libraries`; root paths are returned only to admins, while normal users receive id, name and type. Deleting a library cascades its path and access rows but never touches media files.
- **Alternatives Considered:** `os.path.commonpath` on unresolved strings (symlink bypass), trusting admin input (a typo could expose `/` to the scanner), exposing paths to every user (unnecessary information disclosure), rejecting the mount root itself (legitimate single-root setups).

## ADR 018: Login Throttling and Audit Visibility

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Login is the only unauthenticated credential endpoint, and the audit table needs an operator-facing read path.
- **Decision:** Failed logins are counted in a process-local sliding window for both `ip:<addr>` and `user:<lowercased username>`, with defaults of 5 failures per 60 seconds. Checks happen before password verification and raise 429 with a `Retry-After` header; a successful login clears the username counter (the IP counter ages out naturally). Client IPs come from the socket unless `HEARTH_TRUST_PROXY_HEADERS=true`, because `X-Forwarded-For` is spoofable. `GET /api/v1/admin/audit` exposes newest-first entries with a limit (1-200) and optional action filter to admins only. Audit details never contain passwords or hashes.
- **Alternatives Considered:** Persistent throttle counters (extra writes on every failed login), only per-IP limiting (allows distributed guessing of one account), only per-username limiting (allows spraying many accounts), trusting proxy headers by default (trivial bypass), a separate audit UI (frontend is out of scope until Phase 9).

## ADR 019: Durable Jobs, Claiming and SSE Delivery

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Background work must survive process restarts, avoid duplicate execution, support per-type concurrency and expose progress without Redis. The API and worker run in separate containers sharing one SQLite file.
- **Decision:** Jobs live in the `jobs` table. A worker claims work with a single conditional `UPDATE ... WHERE status='pending' ... RETURNING id`, so two processes cannot run the same row. Startup requeues every `running` row (including rows claimed by a dead process); graceful shutdown cancels handlers and requeues rows claimed by that worker id. Concurrency is enforced per job type by the handler registry plus a global `HEARTH_JOB_WORKER_CONCURRENCY`. Failures retry with capped exponential backoff until `max_attempts`; handlers observe cancellation through a callback and raise `JobCancelled`. Handlers receive a `JobContext` with progress and cancellation callbacks and never hold a session across work. SSE at `/api/v1/events` polls the jobs table (1s) rather than using an in-process queue, so events work when the job runs in the worker container; it sends an initial active-job snapshot, updates, and 15s keepalives. Only admins may stream events, and clients must use `fetch`-based SSE because `EventSource` cannot set the bearer header. Compose gains a `worker` service using the same image and config volume.
- **Alternatives Considered:** In-memory `asyncio.Queue` (loses jobs on restart and does not cross containers), Redis (explicitly out of scope), `SELECT ... FOR UPDATE SKIP LOCKED` (SQLite has no such lock; conditional update is portable), SSE via in-process pub/sub (worker events invisible to the API process), WebSockets (extra protocol for one-way progress).

## ADR 020: OpenAPI Snapshot and Drift Check

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** The phase requires the OpenAPI schema to be generated and committed by a script, and a stale snapshot silently misleads client authors.
- **Decision:** `scripts/export_openapi.py` builds the app with isolated settings and writes `docs/openapi.json` with sorted keys and stable indentation. `--check` compares the committed file with a fresh render. CI runs `--check` before tests, and a unit test asserts both freshness and the presence of every Phase 1 route. The script never opens the database.
- **Alternatives Considered:** Writing the schema from a running server (requires Docker and a live process), committing the schema without a verification step (drifts immediately), generating at request time (adds latency and non-determinism).

## ADR 021: Typed ffprobe Boundary

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Playback and scanning need stable, typed stream information, and a corrupt or slow file must not fail a library scan or block the event loop.
- **Decision:** `services/media/probe.py` invokes `ffprobe` with an argument list and a configurable timeout, parses a deliberately small subset of `-show_format/-show_streams/-show_chapters` into Pydantic models, and raises `ProbeError` for missing binaries, timeouts, non-zero exits and malformed JSON. HDR is classified only from concrete tags/side data (`smpte2084` → HDR10, `arib-std-b67` → HLG, Dolby Vision tags/side data), never inferred from bit depth or resolution. The full model is stored as JSON on the media file so playback decisions do not re-probe unchanged files; scanner-level concurrency and error isolation are the scanner's responsibility.
- **Alternatives Considered:** Keeping raw ffprobe dicts (untyped and unstable across ffmpeg versions), using ffmpeg-python/ffprobe-python wrappers (extra dependency over a simple argument list), raising application errors from the media layer (would turn one bad file into a failed scan).

## ADR 022: Catalog Shape, FTS5 and Move Identity

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Scanning must be idempotent, files may be versions of one movie, renames must not lose state, and Phase 8 needs fast title search. SQLite remains the only store.
- **Decision:** A generic `media_items` row owns files, artwork, genres, credits and per-user state; `movies` holds movie-only columns. `media_files.path` is unique and rows are soft-deleted with `missing_since`, then removed after a grace period. Every file stores a SHA-256 `partial_hash` over its size plus the first/last 64 KiB, indexed with size, so a vanished path and a new path can be paired as a move without re-probing. Titles are indexed by an external-content FTS5 table (`media_items_fts`) with insert/update/delete triggers; queries join on the implicit `media_items.rowid`. `UserItemState` is pulled forward from Phase 3 so the Phase 2 acceptance test can prove that moves preserve watch progress.
- **Alternatives Considered:** One table per media type with duplicated state (loses shared user state), deleting missing files immediately (a temporarily unmounted library would lose history), application-only FTS synchronization (misses bulk/admin writes and is easy to forget), standalone FTS storage (doubles title storage).

## ADR 023: Scanner Reconciliation and Safeguards

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** A scan must be cheap when nothing changed, must not lose watch state on a rename, must tolerate corrupt files, and must not wipe a library because a mount was temporarily unavailable.
- **Decision:** The scanner walks in a thread, then hashes new/changed files and probes only those, with a configurable concurrency limit. It pairs a new path with an absent catalog path when size and the 64 KiB-edge SHA-256 match; the existing file row and item are updated in place, so item ids and user state survive. Unchanged files are not hashed or probed. Removals are soft (`missing_since`) for a configurable grace period, then file rows and empty items are deleted. If more than 30% of at least 10 active files in the scanned scope would disappear, the scan aborts before any writes and records an `aborted` status and reason. Watcher-driven scans limit both the walk and removal detection to the changed directories. Probe/hash failures are per-file errors; the database transaction is only opened after all I/O and holds no external operations.
- **Alternatives Considered:** Full-content hashes (too slow on large libraries), inode-only move detection (not stable across filesystems/backups), immediate hard deletes (loses state on transient unmounts), aborting the whole scan on one corrupt file (one bad file would hide the rest of the library), holding a transaction during probing (would block SQLite writers for seconds/minutes).

## ADR 024: Filename Parsing Rules

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Release names are inconsistent, and a parser that guesses too aggressively loses parts of real titles. Folders are often more trustworthy than scene-style file names.
- **Decision:** `guessit` parses movie names after converting dot/underscore separators to spaces. Hyphens are preserved so titles such as `Spider-Man` survive; guessit's `alternative_title` is joined back with ` - `. A leading `Part N` marker is extracted before guessing and reattached, preventing it from truncating titles such as `Mission Impossible - Dead Reckoning`. Numeric titles (`1984`, `1917`) are never treated as release years unless another year is present. Folder names are used only when the file has no year or is generically named (`movie.mkv`), while obvious root names (`Movies`, `Films`, `Downloads`) are ignored. Any guessit failure falls back to the deterministic year/token parser instead of failing a scan.
- **Alternatives Considered:** Hand-written regex only (brittle across release conventions), trusting guessit's title verbatim (loses hyphenated/part titles), always preferring folder names (breaks flat libraries and multi-film folders).

## ADR 025: Metadata Providers and Caching

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Metadata is optional external data. A missing key, network outage or provider error must never fail a scan, and repeated scans must not hammer TMDB. NFO files are user intent and must win when present.
- **Decision:** `MetadataProvider` exposes `search`, `fetch_details`, `fetch_images` and returns normalized `MovieMetadata`/`ProviderImage` models. `LocalProvider` parses Kodi-style NFO and local sidecar images/subtitles; remote URLs in NFO images are ignored so an NFO cannot trigger arbitrary downloads. `TMDBProvider` supports a v3 key or v4 bearer token, paces requests, retries 429 once with `Retry-After`, maps 401/403 to `ProviderUnavailable`, and caches successful JSON responses in `provider_cache` with a TTL. Credentials and request URLs are never included in errors or logs. Matching will treat `ProviderError`/`ProviderUnavailable` as retryable and leave items pending; that policy lands with the matcher. TMDB attribution is in `docs/ATTRIBUTION.md` and `GET /api/v1/about`.
- **Alternatives Considered:** Calling TMDB on every scan (slow and rate-limited), caching only in memory (lost on worker restart), trusting NFO artwork URLs (SSRF risk), failing scans on provider errors (violates the Phase 2 acceptance criterion), fetching images inside the provider (couples network I/O to metadata mapping and complicates caching).

## ADR 026: Matching, Scoring and Retry Policy

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Catalog items must attach to metadata without hammering providers on every scan, while remaining resilient to missing keys, outages and ambiguous titles. Renames and rescans must not trigger redundant provider work.
- **Decision:** `MetadataMatcher` resolves due `pending`/`unmatched` items only. Local NFO always wins with zero remote calls. Otherwise it searches, ranks candidates with a weighted score (title 0.60, year 0.25, runtime 0.15) against a configurable threshold (default 0.80), fetches details for the top three and accepts the best. Provider failures (including a missing key) leave the item `pending` with `match_error` and an exponential retry (`base 6h`, capped `72h`); an ambiguous result becomes `unmatched` with a `24h` retry. Matched and not-yet-due items are never queried, so an unchanged rescan makes zero provider calls. The scanner accepts an optional matcher and merges match counters; unexpected matcher errors are logged and counted without failing the scan. Images are stored through an optional artwork callback so the image pipeline stays decoupled. Manual `POST /items/{id}/match` bypasses scoring and is audited; `GET /metadata/search` is admin-only and returns 502 on provider errors.
- **Alternatives Considered:** matching during the scan transaction (would hold SQLite while doing network I/O), always re-querying providers (violates the zero-provider-rescan criterion), treating every no-result as a hard failure (ambiguous titles are normal and should be retryable/manually fixable), coupling image download into the matcher (mixes concerns and blocks metadata on slow downloads).

## ADR 027: Artwork Caching and Variants

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Posters and backdrops arrive from TMDB as remote URLs and from NFOs as local files. Clients need small, modern images without re-downloading originals, and an NFO must not be able to make Hearth fetch arbitrary URLs.
- **Decision:** `ImagePipeline` is the matcher's artwork callback. It accepts `ProviderImage`s, copies local files or downloads remote ones into `data_dir/cache/images/originals/<item>/`, and records an `images` row with dimensions, BlurHash and the origin (`source_url` holds the URL or local path). Downloads are http(s)-only, restricted to `image_allowed_hosts` (default `image.tmdb.org`), capped by `image_max_download_bytes`, and never follow redirects. Duplicate origins are skipped per item, and a new primary image clears the previous primary of the same kind. Variants are rendered on demand into `variants/<image>/<width>w.<ext>` for WebP/JPEG/PNG at 16–4096 px, using a shared `ProcessPoolExecutor` (thread fallback if a process pool is unavailable) with atomic temp-file replacement; cached variants are reused. BlurHash and dimension inspection also run in the pool and are best effort, so a blurhash failure never fails a match.
- **Alternatives Considered:** storing originals next to media files (writes into user libraries and can fail on read-only mounts), downloading on every request (slow, rate-limited, offline-hostile), following redirects (SSRF pivot), rendering variants during the scan (delays scans and wastes work for sizes nobody requests), generating all sizes eagerly (storage and CPU waste).

## ADR 028: Scan Jobs, Scheduling and Watching

- **Date:** 2026-10-21
- **Status:** Accepted
- **Context:** Scans need to happen on demand, every six hours by default, and shortly after local files change, without duplicate work or unbounded job queues. The API and worker are separate processes, so scheduling and watching belong to the worker.
- **Decision:** Scans are durable `scan.library` jobs. The handler builds the matcher through the metadata factory, runs `LibraryScanner`, merges progress/cancellation with the job worker, and closes the matcher. A shared `enqueue_scan` helper allows at most one pending/running scan per library; manual `POST /libraries/{id}/scan` reuses an active job (202, `created=false`) and is audited. The scheduler runs in the worker and queues libraries whose `last_scan_at` is missing or older than `scan_interval_hours`; its tick is also idempotent. `watchfiles` watches existing movie roots with a 10-second debounce, groups each batch by the most specific root, and enqueues one targeted scan per library whose `targets` are the affected directories; irrelevant extensions and paths outside roots are ignored. Roots are refreshed periodically so newly added libraries are watched and unmounted roots stop producing scans. Watcher and scheduler failures are logged and retried rather than killing the worker.
- **Alternatives Considered:** scanning inside an API request (blocks HTTP and duplicates jobs), a separate scheduler process (more deployment surface for little benefit), per-file jobs (queue storms and repeated library loads), watching the filesystem from the API process (two watchers when scaled), treating every watcher event as a full scan (wasteful), following foreign keys/parents outside a library root (could scan unrelated data).

## ADR 029: Phase 2 Acceptance Fixtures and Real-Probe Measurement

- **Date:** 2026-10-24
- **Status:** Accepted
- **Context:** Two Phase 2 acceptance criteria make claims the existing tests could only approximate: "zero ffprobe calls" and "50 generated files under 30 seconds". The scanner tests inject scripted probes for speed, and section 3 of the plan specifies a shared `scripts/make_fixtures.py` that does not exist yet. Verifying with fakes would prove the reconciliation logic but not the claim, and building the shared generator now would be unplanned scope.
- **Decision:** Add `tests/integration/test_phase2_acceptance.py`, which encodes one real 1-second H.264 file with ffmpeg, copies it into 50 uniquely named library files, and scans with a thin `CountingProbe` wrapper around the production `probe_media` call. That makes the probe count a measured fact rather than an injected one, and the 30-second budget an assertion instead of an observation. The suite skips when ffmpeg/ffprobe are absent. In-test generation is the interim fixture strategy; `scripts/make_fixtures.py` remains owed and must be built when Phase 3/4 need richer media (multi-audio, subtitle, HDR variants). The new tests also close the "retried later" half of the provider-failure criterion by moving `next_match_at` into the past and proving a successful retry, which the existing tests only asserted as a future timestamp.
- **Alternatives Considered:** counting only through a fake probe (does not verify the real claim), downloading fixtures (violates the plan's no-downloaded-media rule and is flaky), making the benchmark a skipped-by-default slow test (would let the criterion rot), building `scripts/make_fixtures.py` during verification (mixes verification with new feature scope and delays the phase gate).

## ADR 030: Catalog Read API, Cursor Design and Artwork Authentication

- **Date:** 2026-10-24
- **Status:** Accepted
- **Context:** Phase 2 closes with a browse/detail/artwork API that later phases extend. It must paginate stably under concurrent scans, keep the 404-not-403 rule from ADR 015, feed `<img>` elements that cannot send headers, and not leak host paths.
- **Decision:** Listing uses keyset pagination over `(sort_key, id)`. The cursor is base64url JSON carrying the sort key, direction and item id; it is bound to the requested `sort`/`order` and rejected with 422 `invalid_cursor` otherwise, so a client cannot silently page a differently ordered result set. Allowed sorts are title (`sort_title`), added date, year and rating; null years and ratings are coalesced to sentinels (`±9999`, `±11.0`) so `NULL`s sort last in both directions and cursor comparisons stay total. Filters are genre (case-insensitive via `lower()`), year and watched state; `watched=false` includes items with no state row. Responses never expose `media_files.path`; files surface as ids plus container/bitrate/duration for Phase 3.
  Access control reuses `require_library_access` for the collection and a shared `user_can_access_library` helper for item and image lookups, all returning 404. A new `ViewerDep` resolves either a bearer token or a media token from `?token=`; only the image route uses it, so media tokens cannot reach JSON or management endpoints. Image variants reuse the Phase 2 pipeline (shared process pool) with `private, immutable` caching. Genres are sorted in responses so payloads are deterministic.
- **Alternatives Considered:** offset pagination (drifts when scans insert items and gets slower deep into a library), a cursor opaque server-side cache (state for a read-only API), exposing file paths (host layout disclosure and a path-traversal surface), accepting media tokens on every read endpoint (expands a narrow media credential into a general one), returning 403 for ungranted items (violates ADR 015 and reveals existence), unsorted genre lists (nondeterministic payloads and ETags).

## ADR 031: Direct Streaming, Range Semantics and Path Containment

- **Date:** 2026-10-24
- **Status:** Accepted
- **Context:** Direct play must support seeking in browser players, survive a slow disk or network mount without stalling the event loop, authenticate media elements that cannot send headers, and never serve a file outside a library root even if a database row is tampered with.
- **Decision:** `GET|HEAD /api/v1/stream/{file_id}` resolves the `media_file` row, re-checks item scope for media tokens, resolves library access, then resolves the file path and requires it to be inside one of the item's library roots (both sides `resolve(strict=True)`), so `..`/symlink escapes and forged rows return 404. Range parsing is a pure function with explicit semantics: a single `bytes` range only; unknown units, multiple ranges and syntactically invalid specs are ignored (full 200), while a valid range starting at or beyond EOF, or a zero-length suffix, returns 416 with `Content-Range: bytes */size`. Suffix ranges longer than the file and ends past EOF are clamped. Responses carry `Accept-Ranges`, `Content-Length`, `Content-Range`, `ETag` from the stored partial hash, `Last-Modified`, `nosniff` and private revalidation caching; `If-None-Match` yields 304 and a non-matching `If-Range` falls back to a full 200. Bodies stream in `stream_chunk_bytes` (512 KiB) chunks with seek/read/close in `asyncio.to_thread`, and HEAD returns headers only without opening the file.
- **Alternatives Considered:** `FileResponse` (Starlette does ranges but gives no control over ETag/containment or chunking and reports `stat`-based metadata), synchronous `open().read()` in the generator (blocks the loop on slow mounts), multipart/byteranges support (no browser player needs it; significant complexity), signing file ids (access is already enforced per request; ids are random UUIDv7 and scoped by library), trusting the stored path (a DB write could otherwise expose arbitrary files), `sendfile` (not available through ASGI portably).

## ADR 032: Playback State Writes and Auto-Watched Semantics

- **Date:** 2026-10-24
- **Status:** Accepted
- **Context:** Phase 3 must remember where each user stopped, let them correct the watched flag, favorite and rate items, and finish an item without the client guessing when the credits start. `UserItemState` already existed from Phase 2 (pulled forward for move detection), so this task defines behaviour rather than schema.
- **Decision:** All writes are per user and keyed by `(user_id, item_id)`: `POST /items/{id}/progress` stores a clamped `position_ms` plus `last_played_at`; `POST /items/{id}/watched` sets or clears `played`, and clearing also resets `position_ms` to 0 so a rewatch starts at the beginning; `POST /items/{id}/favorite` and `PUT /items/{id}/rating` set boolean/`1..10` values, with `null` clearing a rating. Every write returns the same `WatchStateResponse` shape used by item detail so clients can render state without a second request. Access follows the catalog rule: missing items and items in ungranted libraries return 404, and media tokens are not accepted on writes.
  Watched is derived by a pure function, not by the client: a positive position counts as watched when it reaches a chapter whose title matches credits/outro (earliest such chapter wins) or when it reaches `watched_threshold` (0.90) of a known duration. Positions are clamped to the duration and a position of 0 never auto-marks. Duration comes from the request's `duration_ms` when supplied, otherwise the catalog `runtime_seconds`, otherwise the longest probed file. Chapters come from the longest probed file that has any in `streams_json`. Auto-watch only ever sets `played`; it never clears it, so finishing a rewatch cannot un-finish an item.
  `play_count` increments when a `playing` report arrives after at least `playback_session_gap_seconds` (300) without any report; continuous checkpoints do not inflate it. Progress reports carry an optional `state` (`playing`/`paused`/`stopped`) and report `auto_watched`/`watched_reason` so the UI can acknowledge the transition.
- **Alternatives Considered:** client-decided watched flags (every client would need the same chapter and threshold logic), marking watched at 100% only (credits make that unreachable in practice), storing the last state string (a column whose only use was play-count debouncing), unmarking on a low progress report (breaks rewatches and late-arriving out-of-order reports), resetting position when marking watched (loses resume data for a rewatch), accepting media tokens on writes (turns a read credential into a mutation credential).

## ADR 033: Home Shelf Membership and Ordering

- **Date:** 2026-10-24
- **Status:** Accepted
- **Context:** The client needs three cheap home rows without duplicating catalog queries, and the shelves must obey the same per-user access rule as the browse API (ADR 015) without leaking the existence of other libraries.
- **Decision:** `GET /home/continue-watching` returns items with a state row for the caller where `played` is false, `position_ms > 0` and `last_played_at` is set, ordered by `last_played_at` descending. Finished items (including items auto-marked at the credits) and freshly opened items with no position are excluded, so the row only contains genuine resumables. `GET /home/recently-added` orders by `added_at` then id, both descending, and optionally scopes to `?library_id=`; an unknown or ungranted library returns 404 `library_not_found` through the same `require_library_access` helper as the catalog. `GET /home/favorites` joins the caller's favorite state rows and orders by `user_item_state.updated_at` descending, which is the closest available proxy for "recently favorited" (there is no dedicated favorite timestamp; any state write bumps it). Non-admin shelf queries are constrained to libraries with a `LibraryAccess` grant, including continue-watching and favorites rows, so revoking access removes an item from every shelf. All shelves accept `limit` (1–100, default 20), fetch one extra row to report `has_more`, and reuse `item_summary` and the caller's watch state so cards match the catalog payloads.
- **Alternatives Considered:** a dedicated favorite timestamp column (a migration for ordering only, when state `updated_at` already moves on favorite changes), filtering shelves client-side from the catalog (extra data and duplicated access logic), returning continue-watching for watched items with a position (fills the row with finished rewatches), including items with position 0 (cards that restart from the beginning look like resume failures), offset pagination on shelves (the client only needs the first screen; the catalog endpoint has keyset pagination for See All).

## ADR 034: Subtitle IDs, Live Discovery and WebVTT Conversion

- **Date:** 2026-10-24
- **Status:** Accepted
- **Context:** Browser players need WebVTT, but libraries contain SRT, ASS/SSA and occasional VTT sidecars whose names and presence can change without a rescan. Subtitle URLs are consumed by `<track>` elements, which cannot send an Authorization header, and ids must not become a path-traversal surface.
- **Decision:** Item detail lists sidecars discovered live next to each file (not the scanner's cached `sidecar_subtitles` column), exposing an opaque id, basename, format, language tag and URL. The id is base64url JSON `{"f": file_id, "n": basename}`; decoding rejects non-JSON, missing fields, absolute paths, separators and dotfiles. `GET /subtitles/{id}.vtt` resolves the file row, applies item scope for media tokens and the usual library-access rule, requires the registered video path to resolve inside its library roots, and then requires the requested basename to appear in a *fresh* `find_sidecar_subtitles` result. Deleting or replacing a sidecar therefore invalidates old URLs (404) instead of serving stale or renamed content. The sidecar path is containment-checked again before reading.
  Conversion is pure: SRT keeps cue text and indexes while normalizing timestamps to `HH:MM:SS.mmm`; VTT passes through when it already has a `WEBVTT` header; ASS/SSA parses the `Format:` field order (with SSA defaults when absent), ignores comments and malformed/backwards cues, strips `{...}` override blocks, expands `\N`/`\n`/`\h`, and HTML-escapes text. Styling, positioning and karaoke tags are intentionally dropped: the plan asks for playable WebVTT, not a renderer. Bytes are decoded as UTF-8 (BOM tolerated) with a Windows-1252 fallback. Files above `subtitle_max_bytes` (4 MiB) return 413. MicroDVD `.sub` is discovered by the scanner but not exposed because it is not text-timestamped and would need a different converter.
- **Alternatives Considered:** serving the cached `sidecar_subtitles` list (stale after a rename or new file until the next scan), an exposed host path or signed path (path traversal and host-layout disclosure), numeric subtitle table (schema for data that is already on disk and cheap to rediscover), ffmpeg conversion (process spawn per request and an ffmpeg dependency for a task that is pure text), passing ASS through unconverted (browsers do not play it), preserving ASS styles by mapping every tag (large surface for cosmetic parity), keeping `.sub` with a best-effort parser (frequently encoded in legacy code pages and not timestamp-compatible).

## ADR 035: Developer Test Player Delivery and Scope

- **Date:** 2026-10-24
- **Status:** Accepted
- **Context:** Phase 3 needs an end-to-end way to log in, list items, seek, switch subtitles and prove progress reporting without committing to a frontend stack that Phase 7 owns.
- **Decision:** `tools/testplayer/index.html` is a single static file with inline CSS and JavaScript, no build step and no dependencies. The application mounts it at `/testplayer/` only when `tools/testplayer/index.html` exists relative to the working directory or the source checkout, so an installed deployment without the directory simply has no route. The Docker image copies `tools/` so the container parity is the same. The page is labelled a throwaway developer tool, keeps the access token in memory only (no `localStorage`), logs every request outcome in a collapsible panel, logs in through `/auth/login`, lists the caller's libraries and their items, and plays the first non-missing file with a native `<video>` element. The page asks `/items/{id}/playback-info` for a decision and plays the tokenized direct-play URL it returns, using the same response for its subtitle tracks; that exercises the query-string authentication and the final Phase 4 branch shape real clients will use. Playback reports progress on a 5-second interval plus pause and ended events, resumes from the stored position, and exposes watched, favorite, rating and subtitle controls. Range seeking comes from the browser's native controls.
- **Alternatives Considered:** a separate JavaScript project with a bundler (scope and maintenance for a tool that is meant to be thrown away), `file://` use (the API is on another origin and browser CORS blocks it), embedding the HTML as a Python string (the plan asks for a real static file and it would be harder to edit), serving it from the installed `app` package (loses the documented `tools/testplayer` location), storing tokens in `localStorage` (a dev convenience that teaches the wrong pattern), hiding the route in production via a setting (the page contains no secrets and the directory check already keeps it out of stripped deployments).

## ADR 036: Playback-Info Request/Response Contract

- **Date:** 2026-10-24
- **Status:** Accepted
- **Context:** Phase 4 will add remuxing and transcoding, and clients should not need a breaking change when that happens. The decision engine also needs a full client capability profile, while Phase 3 can only serve the original file.
- **Decision:** `POST /items/{id}/playback-info` accepts an optional body with `file_id` and a `capabilities` object (preset name, containers, video codecs and profiles, audio codecs, max width/height/bitrate, subtitle formats, HDR and HLS-fMP4 support). The capability fields are validated but deliberately unused in Phase 3; they exist so Phase 4's decision engine can start consuming them without reshaping requests. The response always names a `mode` from a closed set that already includes the Phase 4 modes: `direct_play`, `remux_hls`, `transcode_hls`. `url` carries the direct stream URL, while `hls_url` and `session_id` are present but null until HLS exists, so clients can implement the final branch structure now. Every response also includes the selected `file` (id, container, duration, size), tokenized subtitle URLs, human-readable `reasons`, and the media token's `expires_at`.
  File selection is explicit: `file_id` must be a non-missing version of the requested item, otherwise 404; omitting it picks the first playable version by path so the choice is deterministic. The selected path is re-resolved against the library roots before any URL is minted, matching the stream route's containment rule. URLs are relative (like image URLs) and carry an item-scoped media token in `?token=`, so `<video>` and `<track>` can use them without headers. The route itself requires a bearer token: media tokens are for media elements, not for minting more URLs.
- **Alternatives Considered:** a Phase 3-only response and a breaking Phase 4 response (the task explicitly forbids it), a boolean `direct_play` plus separate fields for every later mode (ambiguous and duplicative; one enum is the decision), absolute URLs (the server does not know the public origin behind a proxy), returning the raw media token separately (two sources of truth for the same credential), accepting a media token on this route (a URL credential should not mint URLs), using the stored `sidecar_subtitles` cache (live discovery keeps subtitle URLs correct after a rename).

## ADR 037: TV Shows Data Model Hierarchy and MediaFile Linkage

- **Date:** 2026-10-26
- **Status:** Accepted
- **Context:** Phase 4 introduces TV shows with a hierarchy of Series, Seasons, and Episodes. Media files on disk may represent a single episode, multiple episodes (e.g. `S01E01E02`), or multiple versions of an episode (e.g. 1080p and 4K). User watch progress, artwork, and library access must integrate seamlessly with existing models.
- **Decision:** Model `Series`, `Season`, and `Episode` as type-specific extensions attached 1-to-1 to generic `MediaItem` rows (`type in ('series', 'season', 'episode')`). `Series` owns `seasons` and `episodes`. `Season` references `series.item_id` with a uniqueness constraint on `(series_id, season_number)`. `Episode` references `series.item_id`, optional `season.item_id` (allowing unmatched/special episodes without a defined season), and links to `media_files.id` via nullable `file_id` (foreign key with `ondelete='SET NULL'`). Single-episode files store `media_files.item_id = episode.item_id`; multi-episode files store `media_files.item_id = first_episode.item_id` (ensuring library containment and streaming continue to work), while all constituent episode rows reference the shared file via `file_id`.
- **Alternatives Considered:** Separate standalone tables not inheriting from `MediaItem` (loses unified watch progress, FTS indexing, and library access rules), a dedicated many-to-many join table for episode files (unnecessary complexity since direct foreign key on episode covers multi-episode files and `media_files.item_id` covers multiple versions).

## ADR 038: TV Episode Parsing Rules and Folder Hints

- **Date:** 2026-10-26
- **Status:** Accepted
- **Context:** TV episodes follow varied release naming conventions: standard `S01E01`, multi-episode `S01E01E02` / `1x01-02`, specials `S00E01` / `SP01` / `Specials/`, date-based daily shows `YYYY-MM-DD` / `YYYY.MM.DD`, and anime absolute numbering.
- **Decision:** `parse_episode_path` pairs `guessit` with targeted regexes and directory hierarchy hints. When an episode resides inside a series directory, the series directory establishes `series_title` and `series_year`. If guessit misassigns an episode title as the show title (e.g. `Fringe/1x04 - The Arrival.mkv`), the folder sets `series_title` and the filename token becomes `episode_title`. Multi-episode patterns yield sorted tuples of episode numbers. Daily shows extract ISO `YYYY-MM-DD` dates and clear year-as-season confusion. Specials detect season 0, `Specials` folders, and `SP(\d+)` markers. Anime releases without season indicators preserve `absolute_number`.
- **Alternatives Considered:** Relying on guessit alone without folder context (fails on generic filenames like `S01E01.mkv` and confuses episode titles with show titles), separate parsers per format (complex routing before reading filename tokens).
## ADR 039: TMDB TV Metadata Mapping and Episode Groups Policy

- **Date:** 2026-10-26
- **Status:** Accepted
- **Context:** TV shows on TMDB require fetching series, season, and episode details, stills, cast/crew credits, and artwork. TMDB also offers alternative "episode groups" (e.g. production order, DVD order, absolute numbering). We need to determine how TMDB TV metadata is fetched, structured, and cached, and decide on the handling of TMDB episode groups.
- **Decision:** Extend `MetadataProvider` with `search_tv`, `fetch_tv_details`, `fetch_tv_season`, `fetch_tv_episode`, and `fetch_tv_images`, returning typed `SeriesMetadata`, `SeasonMetadata`, and `EpisodeMetadata`. In `TMDBProvider`, query TMDB endpoints (`/search/tv`, `/tv/{id}`, `/tv/{id}/season/{s}`, `/tv/{id}/season/{s}/episode/{e}`, `/tv/{id}/images`) with caching in `provider_cache` using structured keys (`tv:{id}`, `tv:{id}:season:{s}`, etc.). Season 0 is preserved for specials. Season calls eagerly parse constituent episode lists including stills, runtime, and credits, reducing subsequent network lookups.
  Per the implementation plan instructions, TMDB episode groups are skipped for the initial build. Standard aired order (season + episode number) aligns with the vast majority of media server libraries (Kodi, Jellyfin, Plex conventions). Supporting episode groups requires complex user selection UI and disambiguation, which can be evaluated in future iterations if user demand arises.
- **Alternatives Considered:** Supporting episode group selection in the initial build (adds substantial complexity and configuration surface before the core TV scanner/library experience is stable), making separate network calls for every single episode detail (excessive TMDB API rate-limiting; fetching full seasons eagerly is significantly more efficient).

## ADR 040: TV Series Scanner, Cascade Reconciliation and Manual Rematch

- **Date:** 2026-10-26
- **Status:** Accepted
- **Context:** When scanning a TV library, media files represent episodes belonging to seasons of a series. Reconciliations must handle new episodes, multi-episode files, deleted episodes, removed seasons, removed series, specials, date-based episodes, and series manual re-matches without orphaning rows or breaking user watch states.
- **Decision:**
  - Group files in TV libraries by series directory. Create or update `Series` items under the library.
  - Parse episode paths using `parse_episode_path` and probe media files with `ffprobe`.
  - For each parsed episode, create or update `Season` and `Episode` items. For multi-episode files (e.g. `S01E01E02`), create an `Episode` item for each episode number in the file, both pointing `Episode.file_id` to the single `MediaFile.id`, and pointing `MediaFile.item_id` to the primary (first) episode item to preserve library containment and direct streaming.
  - Unknown/unmatched episodes (e.g. unrecognizable filenames) create an episode item with `match_state="unmatched"`, `season_id=None`, `season_number=None`, `episode_number=None`.
  - Cascade orphan cleanup: when episode files are removed, delete their `Episode` and `MediaItem` records. If a `Season` has no remaining active episodes, delete the season. If a `Series` has no remaining active seasons or episodes, delete the series.
  - Series matching (`match_pending`): match series to TMDB TV entries, fetch series details and all season details eagerly, and map episodes by `(season_number, episode_number)` or `air_date`.
  - Manual series re-match (`manual_match`): update the series metadata, then match and update existing constituent episode items in-place where episode numbers still exist, preserving all `user_item_states` (playback position, watched flag, favorites) tied to those episode items.
- **Alternatives Considered:**
  - Deleting all episodes and re-creating them on series re-match (destroys watch state and progress for users).
  - Leaving empty seasons or empty series when all files are deleted (leaves ghost entries in the library).

## ADR 041: TV Shows API Endpoints, Next-Up Progression, and Shelf Integration

- **Date:** 2026-10-26
- **Status:** Accepted
- **Context:** TV show browsing requires hierarchy navigation (Series -> Seasons -> Episodes), a deterministic "Next Up" algorithm capable of handling season boundaries, skips, out-of-order watching, and re-watches, merged continue watching with movies, and batch watch-state marking (seasons and series).
- **Decision:**
  - Expose `GET /series/{id}`, `GET /series/{id}/seasons`, `GET /seasons/{id}`, `GET /seasons/{id}/episodes`, and `GET /episodes/{id}`. Top-level `GET /libraries/{id}/items` returns series when `library.type == "tv"`.
  - Next-Up calculation (`select_next_up_episode`): prioritizes regular seasons (season 1+) over specials (season 0) unless only specials exist. For unplayed series, returns S01E01. For active series, finds the last watched episode by most recent `last_played_at` (or `updated_at`), then selects the first unwatched episode after it in canonical `(season_number, episode_number)` order. If later episodes were watched ahead leaving earlier episodes skipped, it falls back to the earliest unwatched episode in the series once trailing episodes are watched.
  - Surface `GET /shows/next-up` for active series on home feeds and `GET /series/{id}/next-up` for show detail pages.
  - Merge movies and in-progress episodes in `GET /home/continue-watching` by `last_played_at.desc()`.
  - `POST /seasons/{id}/watched` and `POST /series/{id}/watched` cascade watched status to all constituent episodes and update parent season/series records.
- **Alternatives Considered:**
  - Using simple highest episode number for next up (fails when users re-watch earlier episodes or skip ahead).
  - Separate next-up shelves for movies vs TV (unifies the home feed experience and matches standard media server client expectations).

## ADR 042: Local TV Metadata Discovery via tvshow.nfo and episode.nfo

- **Date:** 2026-10-26
- **Status:** Accepted
- **Context:** Kodi and Emby/Jellyfin libraries frequently supply local metadata via `tvshow.nfo` in the series directory, `<episodename>.nfo` alongside video files, and sidecar images (`poster.jpg`, `fanart.jpg`, `seasonXX.jpg`, `<episodename>-thumb.jpg`). When local metadata is present, scanners should be capable of 100% offline matching without external TMDB API requests.
- **Decision:**
  - Extend `LocalProvider` with `find_series(series_dir)` and `find_episode(episode_file_path)`.
  - Parse `<tvshow>` Kodi XML: title, year, premiered, plot, status, studio, rating, genres, external IDs (`tmdb`, `tvdb`, `imdb`), actors, and series-level posters/backdrops.
  - Discover season-level posters (`season01.jpg`, `season-specials.jpg`, etc.) via `find_season_poster`.
  - Parse `<episodedetails>` Kodi XML: title, season, episode, aired date, plot, runtime, rating, actors, directors, writers, and episode stills (`<base>-thumb.jpg`, `<base>-still.jpg`, `<base>.jpg`).
  - Integrate `LocalProvider` into `SeriesMatcher`: check for local series NFO before online TMDB lookups. If found, yield a match candidate with confidence 1.0. During episode metadata application, check for episode NFOs to populate episode plots, titles, ratings, and credits.
- **Alternatives Considered:**
  - Always requiring online TMDB lookups even when NFO exists (fails for offline environments, home movies, and airings not cataloged on TMDB).
  - Separate NFO parser classes for series vs episodes (keeping them on `LocalProvider` unifies local file-based metadata handling alongside existing movie NFO support).

