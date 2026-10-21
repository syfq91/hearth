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
