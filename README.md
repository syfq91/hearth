# Hearth

Self-hosted personal media server for movies, TV, music, and books.

Hearth is an API-first FastAPI application with an async background worker and
hardware-accelerated (VAAPI) transcoding. The implementation follows the phased
plan in [Hearth AI Agent Implementation Plan for a Self-Hosted Media Server.md](Hearth%20AI%20Agent%20Implementation%20Plan%20for%20a%20Self-Hosted%20Media%20Server.md);
current status is tracked in [docs/PROGRESS.md](docs/PROGRESS.md).

## Quick start (Docker)

Requirements: Docker with the Compose v2 plugin, plus a directory containing your media. A GPU is optional; software transcoding is the automatic fallback.

```bash
cp .env.example .env          # set MEDIA_DIR to your media library
export RENDER_GID=$(getent group render | cut -d: -f3)   # e.g. 105 on Debian/Ubuntu
docker compose --env-file .env -f docker/compose.yaml up --build -d
```

Open **`http://localhost:8000`** in your browser. The responsive web application guides you through initial administrator setup, adding libraries, and browsing your collection.

### Hardware Transcoding Options

Hearth automatically detects and utilizes GPU hardware acceleration for video transcoding:

- **Intel QuickSync (Default / 8th Gen+)**:
  ```bash
  docker compose -f docker/compose.yaml -f docker/compose.intel.yaml up -d
  ```
- **AMD Radeon / Ryzen APU**:
  ```bash
  docker compose -f docker/compose.yaml -f docker/compose.amd.yaml up -d
  ```
- **CPU Software Transcoding (Hosts without GPU / `/dev/dri`)**:
  ```bash
  docker compose -f docker/compose.yaml -f docker/compose.cpu.yaml up -d
  ```

For detailed GPU permissions, render node discovery, and driver checks, see the [Hardware Transcoding Guide](docs/HARDWARE_TRANSCODING.md).

The compose stack runs two processes: `hearth` (FastAPI and embedded Web UI) and `worker` (background scanner, metadata scraper, and cleanup jobs). Both safely share the `/config` SQLite database.

### First Run & Account Setup

Hearth has no default credentials. On first startup, visit `http://localhost:8000` to complete the initial setup wizard, or use the REST API:

```bash
curl -X POST http://localhost:8000/api/v1/auth/setup \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "a long passphrase"}'
```

Interactive OpenAPI documentation is available at `http://localhost:8000/docs`.

### Features & Web UI

- **Movies & TV Shows**: Adaptive HLS video player with `hls.js`, audio track switching, subtitle selectors, resume playback, and next-episode prompts.
- **Music**: Queue player with disc grouping, album artwork, and Media Session API support for OS lockscreen and media key controls.
- **Books & Comics**: In-browser reader supporting EPUB (via foliate-js), PDF (via pdf.js), and CBZ/CBR comics with cross-client reading progress synchronization.
- **10-Foot TV Mode**: Dedicated spatial navigation mode with D-pad / arrow key support for living room and smart TV browsers.
- **Unified Search**: Instant full-text search across all libraries, media items, authors, and actors.
- **Administration**: Library scanning, unmatched item fixer, live transcode session inspection, log viewer, and online SQLite backup/restore.

### Library and catalog

Create a movie library over the read-only media mount, scan it, then browse it:

```bash
curl -X POST http://localhost:8000/api/v1/libraries \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name": "Movies", "type": "movie", "paths": ["/media/movies"]}'

curl -X POST http://localhost:8000/api/v1/libraries/$LIBRARY_ID/scan \
  -H "Authorization: Bearer $TOKEN"

curl "http://localhost:8000/api/v1/libraries/$LIBRARY_ID/items?sort=title&limit=20" \
  -H "Authorization: Bearer $TOKEN"
```

`GET /api/v1/items/{id}` returns metadata, credits, versions and artwork for one
item; `GET /api/v1/images/{id}` serves resized artwork and accepts a media token
in `?token=` so it can be used directly in `<img>` tags. Scans also run every six
hours and shortly after files change, and `POST /api/v1/libraries/{id}/scan`
returns the active job if one is already queued. Without `HEARTH_TMDB_API_KEY`,
items stay `pending` and are retried with backoff; local `.nfo` files and
sidecar artwork are always used first.

### Playback

Ask the server how to play an item and use the returned URL directly:

```bash
curl -X POST http://localhost:8000/api/v1/items/$ITEM_ID/playback-info   -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json'   -d '{"capabilities": {"preset": "chrome", "hls_fmp4": true}}'
```

The response contains `mode: "direct_play"`, a stream `url` with an item-scoped
media token, tokenized sidecar subtitle URLs and `expires_at`. Report progress
with `POST /api/v1/items/$ITEM_ID/progress`; the server auto-marks an item
watched at 90% or at a credits chapter. Home shelves are at
`/api/v1/home/continue-watching`, `/api/v1/home/recently-added` and
`/api/v1/home/favorites`.

### Developer test player

`tools/testplayer/index.html` is a throwaway single-file page for exercising
login, direct streaming, seeking, subtitles and progress reporting without the
real frontend. It is served at `http://localhost:8000/testplayer/` whenever the
`tools/testplayer` directory is present (the source checkout and the Docker
image), and keeps the access token in memory only. See
[ADR 035](docs/DECISIONS.md) for its scope.

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
| `HEARTH_TMDB_API_KEY` | unset | TMDB v3 API key or v4 read token; metadata stays pending without it |

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

- [docs/HARDWARE_TRANSCODING.md](docs/HARDWARE_TRANSCODING.md) — GPU acceleration, VAAPI drivers, render GID, and troubleshooting
- [docs/REVERSE_PROXY.md](docs/REVERSE_PROXY.md) — Caddy, Traefik, and Nginx reverse proxy configurations with TLS and SSE streaming
- [docs/BACKUP_AND_UPGRADE.md](docs/BACKUP_AND_UPGRADE.md) — SQLite online backups, automated snapshot restore, and upgrade procedures
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — Common error resolution, transcode debugging, permissions, and database locks
- [docs/API.md](docs/API.md) — REST API reference, authentication flows, and playback contracts
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — Module layout and runtime architecture diagram
- [docs/PROGRESS.md](docs/PROGRESS.md) — Implementation phase and acceptance checklist
- [docs/DECISIONS.md](docs/DECISIONS.md) — Architecture decision records (ADRs)
- [docs/ATTRIBUTION.md](docs/ATTRIBUTION.md) — TMDB and third-party notices
