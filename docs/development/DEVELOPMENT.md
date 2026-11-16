# Development Guide

This guide covers running Hearth from source, the test and lint commands, and
the generated artifacts that must be kept in sync. For the runtime design, see
[ARCHITECTURE.md](ARCHITECTURE.md); for the HTTP contract, see [API.md](API.md).

## Prerequisites

- Python 3.12 and [uv](https://docs.astral.sh/uv/) 0.10+
- Node.js 22 and npm (only for the web client)
- `ffmpeg` and `ffprobe` on `PATH` (the integration suite probes real media)
- Optional: a VA-API render node for hardware-transcoding tests

## Backend setup

```bash
uv sync
```

`uv sync` installs the locked environment, including the `dev` dependency
group. Run the API against local paths instead of the container defaults:

```bash
export HEARTH_DATA_DIR=./.dev-data
export HEARTH_DB_PATH=./.dev-data/hearth.db
export HEARTH_MEDIA_DIR=./media
export HEARTH_TRANSCODE_DIR=./.dev-transcode
mkdir -p "$HEARTH_DATA_DIR" "$HEARTH_MEDIA_DIR" "$HEARTH_TRANSCODE_DIR"

uv run hearth api --reload     # http://localhost:8000, OpenAPI at /docs
```

Migrations run automatically at startup. Useful commands:

```bash
uv run hearth migrate          # apply migrations and exit
uv run hearth worker           # background jobs without the API
uv run hearth api --embed-worker   # single process, like the container
```

With `HEARTH_EMBED_WORKER=false` (the default outside Compose) the API only
enqueues work; run `hearth worker` in a second terminal if you want scans and
metadata matching to finish.

## Web client

The SPA is served by FastAPI from `web/dist`, so a local run is:

```bash
cd web
npm ci
npm run build
cd ..
uv run hearth api --reload
```

The client calls same-origin `/api/v1` by default, so there is no dev proxy to
configure. Run `npm run check` for Svelte/TypeScript diagnostics and
`npm run build` after UI changes so the API serves the new bundle.

## Tests and checks

```bash
uv run pytest                 # unit + integration suite
uv run pytest tests/unit      # fast subset
uv run ruff check             # lint
uv run ruff format --check    # formatting
uv run mypy app               # strict type checking
```

Tests create their own temporary databases and media fixtures, so they do not
use your configured data directory. Hardware tests are marked `vaapi` and
container-level backup tests are marked `container_e2e` (opt in with
`HEARTH_CONTAINER_E2E=1`).

Web checks:

```bash
cd web
npm run check
npm run build
cd ..
./scripts/e2e.sh              # build the image and run Playwright against it
```

`scripts/e2e.sh` starts separate API and worker containers sharing one
`/config` directory; see the script header for flags and environment variables.

## Generated artifacts

Two files are generated and diff-checked in CI. Regenerate both whenever a
route, schema, or response model changes:

```bash
uv run python scripts/export_openapi.py       # docs/openapi.json
uv run python scripts/generate_client.py      # packages/client/src/schema.d.ts
```

Check them without writing:

```bash
uv run python scripts/export_openapi.py --check
uv run python scripts/generate_client.py --check
```

The Svelte app imports the generated schema through
`web/src/lib/api/types.ts` (`@hearth/client`).

## Developer test player

`tools/testplayer/index.html` is a single-file page for exercising login,
direct streaming, seeking, subtitles, and progress reporting without the real
frontend. It is served at `http://localhost:8000/testplayer/` whenever the
`tools/testplayer` directory is present, keeps the access token in memory only,
and is not part of production workflows (ADR 035).

## Continuous integration

`.github/workflows/ci.yml` runs:

- ruff lint and format, strict mypy, the committed OpenAPI check, pytest, and
  a wheel build on Python 3.12;
- Svelte type checks and a static SPA build;
- a Docker build with a health-contract smoke test, non-root user check, and a
  backup/restore round-trip;
- Playwright end-to-end specs against a fresh container.

CI is the source of truth for the required checks; run the same commands
locally before opening a pull request.

## Where things live

See the module layout in [ARCHITECTURE.md](ARCHITECTURE.md). The short version:

| Path | Contents |
| --- | --- |
| `app/api/v1/` | FastAPI routers |
| `app/services/` | scanning, metadata, playback, transcoding, jobs, images |
| `app/db/` | SQLAlchemy models, session setup, Alembic migrations |
| `app/core/` | settings, auth, errors, logging, security middleware |
| `web/` | SvelteKit SPA |
| `packages/client/` | generated TypeScript API types |
| `tests/` | unit, integration, and container end-to-end tests |
| `docs/` | operator guides, API reference, ADRs |
