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
