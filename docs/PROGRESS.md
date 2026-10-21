# Hearth Implementation Progress

## Status Overview

- **Current Phase**: Phase 0: Bootstrap and tooling
- **Overall Status**: In Progress

---

## Phase 0: Bootstrap and tooling

**Goal:** An empty but fully wired project that builds, lints, tests, and runs in Docker.

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 0.1 | Initialize repo with `uv`, `pyproject.toml`, Ruff, Mypy, Pytest, `.gitignore`, `.editorconfig` | Complete | `chore: initialize repo, uv tooling, and project configurations` |
| 0.2 | Create package layout with placeholder modules | Pending | Pending |
| 0.3 | Implement `core/config.py`, `core/logging.py`, and unified error handler | Pending | Pending |
| 0.4 | Implement `app/main.py` with app factory, `/api/v1/health` and `/api/v1/version` | Pending | Pending |
| 0.5 | Implement Typer CLI (`api`, `worker`, `migrate`, `create-admin`) | Pending | Pending |
| 0.6 | Dockerfile (multi-stage, non-root, tini, VAAPI) and `compose.yaml` | Pending | Pending |
| 0.7 | GitHub Actions CI workflow | Pending | Pending |
| 0.8 | Write `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, and finalize Phase 0 | Pending | Pending |

### Phase 0 Acceptance Checklist

- [ ] `uv run pytest`, `ruff check`, `mypy app` all pass.
- [ ] Container runs as non-root user and health endpoint reports ffmpeg version.
- [ ] Inside the container, `vainfo` runs (or reports `vaapi: unavailable` on non-GPU host).

---

## Open Questions

None currently.

## Hardware Verification Notes

- Host has `/dev/dri/renderD128` (video GID 44, render GID 105).
- Hardware verification will test `vainfo` and VAAPI capability inside container with `--group-add 105`.
