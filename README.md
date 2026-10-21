# Hearth

Self-hosted personal media server for movies, TV, music, and books.

Hearth is an API-first FastAPI application with an async background worker and
hardware-accelerated (VAAPI) transcoding. The implementation follows the phased
plan in [Hearth AI Agent Implementation Plan for a Self-Hosted Media Server.md](Hearth%20AI%20Agent%20Implementation%20Plan%20for%20a%20Self-Hosted%20Media%20Server.md);
current status is tracked in [docs/PROGRESS.md](docs/PROGRESS.md).

## Quick start (Docker)

Requirements: Docker with the Compose v2 plugin, plus a writable media directory.
A GPU is optional; software transcoding is the fallback.

```bash
cp .env.example .env          # set MEDIA_DIR to your media library
export RENDER_GID=$(getent group render | cut -d: -f3)   # e.g. 105 on Debian/Ubuntu
docker compose --env-file .env -f docker/compose.yaml up --build -d
curl http://localhost:8000/api/v1/health
```

The render GID differs between distributions, so the compose file reads
`RENDER_GID` (default `105`). On a host without `/dev/dri`, use the CPU-only
override instead:

```bash
docker compose --env-file .env -f docker/compose.yaml -f docker/compose.cpu.yaml up --build -d
```

The `vaapi` section of `/api/v1/health` reports `unavailable` in that case and
the server stays healthy.

The compose stack starts two processes from the same image: `hearth` (API) and
`worker` (background jobs). Both share the `/config` volume and database.

### First run

Hearth has no default account. Create the first administrator once; the endpoint
returns 409 afterwards:

```bash
curl -X POST http://localhost:8000/api/v1/auth/setup \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "a long passphrase"}'
```

The response contains a 15-minute access token and a rotating refresh token. Use
`Authorization: Bearer <access_token>` for API calls and
`POST /api/v1/auth/login` for later sessions. Interactive API documentation is at
`http://localhost:8000/docs`.

### Configuration

All settings use the `HEARTH_` prefix (see `app/core/config.py`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `HEARTH_ENV` | `development` | `development`, `production`, or `testing` |
| `HEARTH_HOST` / `HEARTH_PORT` | `0.0.0.0` / `8000` | API bind address |
| `HEARTH_LOG_LEVEL` | `INFO` | Root log level |
| `HEARTH_DATA_DIR` | `/config` | Persistent application data |
| `HEARTH_DB_PATH` | `/config/hearth.db` | SQLite database file |
| `HEARTH_TRANSCODE_DIR` | `/transcode` | Transcode scratch space (tmpfs in compose) |
| `HEARTH_MEDIA_DIR` | `/media` | Validated media mount root |
| `HEARTH_RENDER_DEVICE` | `/dev/dri/renderD128` | VAAPI render node |
| `HEARTH_SECRET_KEY` | generated in `/config` | Signing secret; set it to share sessions across nodes |
| `HEARTH_ACCESS_TOKEN_MINUTES` | `15` | Access-token lifetime |
| `HEARTH_REFRESH_TOKEN_DAYS` | `30` | Refresh-token lifetime |
| `HEARTH_MEDIA_TOKEN_SECONDS` | `21600` | Media URL token lifetime (6 hours) |
| `HEARTH_MAX_ENABLED_USERS` | `15` | Enabled-account cap |
| `HEARTH_TRUST_PROXY_HEADERS` | `false` | Honor `X-Forwarded-For` (only behind a trusted proxy) |

If `HEARTH_SECRET_KEY` is not set, a random secret is generated once and stored
in `HEARTH_DATA_DIR/secret_key` with mode 0600. Back it up with the database, or
all sessions and media URLs become invalid.

Media is mounted read-only. The container runs as UID/GID 1000 with a read-only
root filesystem, all capabilities dropped, and `no-new-privileges`.

## Development

```bash
uv sync
uv run hearth api --reload     # http://localhost:8000/docs
uv run pytest
uv run ruff check
uv run ruff format --check
uv run mypy app
```

## Documentation

- [docs/PROGRESS.md](docs/PROGRESS.md) — phase and task status
- [docs/DECISIONS.md](docs/DECISIONS.md) — architecture decision records
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module layout and runtime diagram
