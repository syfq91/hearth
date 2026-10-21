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
