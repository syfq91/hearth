# Configuration Reference

Hearth is configured with environment variables. Every setting uses the
`HEARTH_` prefix and can be supplied through your shell, Docker Compose, or a
`.env` file next to `docker/compose.yaml`. Values are read once at process
start, so restart the container after changing them.

List-valued settings (for example `HEARTH_CORS_ORIGINS`) are parsed as JSON,
e.g. `HEARTH_CORS_ORIGINS='["https://media.example.com"]'`.

The complete, authoritative list lives in `app/core/config.py`; this page
documents the settings most operators touch.

## Core

| Variable | Default | Purpose |
| --- | --- | --- |
| `HEARTH_ENV` | `development` | `development`, `production`, or `testing` |
| `HEARTH_HOST` / `HEARTH_PORT` | `0.0.0.0` / `8000` | API bind address |
| `HEARTH_LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `HEARTH_DATA_DIR` | `/config` | Persistent application data (database, artwork cache, secret) |
| `HEARTH_DB_PATH` | `/config/hearth.db` | SQLite database file |
| `HEARTH_DB_POOL_SIZE` | `5` | SQLite async connection pool size |
| `HEARTH_DB_MAX_OVERFLOW` | `10` | Maximum overflow connections above pool size |
| `HEARTH_MEDIA_DIR` | `/media` | Root that library paths are validated against |
| `HEARTH_TRANSCODE_DIR` | `/transcode` | Scratch space for transcoding sessions (tmpfs in Compose) |
| `HEARTH_EMBED_WORKER` | `false` | Run the background worker inside the API process; Compose sets `true` |
| `HEARTH_CORS_ORIGINS` | `["*"]` | Allowed browser origins for the API |
| `HEARTH_TRUST_PROXY_HEADERS` | `false` | Honor `X-Forwarded-For`; enable only behind a trusted reverse proxy |
| `HEARTH_MAX_ENABLED_USERS` | `15` | Maximum number of enabled accounts |

## Security and sessions

| Variable | Default | Purpose |
| --- | --- | --- |
| `HEARTH_SECRET_KEY` | generated in `/config` | Signing key for access and media tokens |
| `HEARTH_ACCESS_TOKEN_MINUTES` | `15` | Access-token lifetime |
| `HEARTH_REFRESH_TOKEN_DAYS` | `30` | Refresh-token lifetime |
| `HEARTH_MEDIA_TOKEN_SECONDS` | `21600` | Media URL token lifetime (6 hours) |
| `HEARTH_PASSWORD_MIN_LENGTH` | `8` | Minimum password length |
| `HEARTH_LOGIN_RATE_LIMIT_ATTEMPTS` | `5` | Failed logins allowed per window |
| `HEARTH_LOGIN_RATE_LIMIT_WINDOW_SECONDS` | `60` | Login throttling window |
| `HEARTH_MAX_REQUEST_BODY_BYTES` | `52428800` | Request body cap (50 MiB) |

If `HEARTH_SECRET_KEY` is not set, a random secret is generated once and stored
in `HEARTH_DATA_DIR/secret_key` with mode `0600`. Back it up with the database:
without it, existing sessions and media URLs become invalid after a restore.

## Library scanning

| Variable | Default | Purpose |
| --- | --- | --- |
| `HEARTH_SCAN_INTERVAL_HOURS` | `6` | Automatic rescan interval |
| `HEARTH_SCAN_SCHEDULER_ENABLED` | `true` | Periodically queue scans for due libraries |
| `HEARTH_SCAN_WATCH_ENABLED` | `true` | Watch library roots and rescan after file changes |
| `HEARTH_SCAN_WATCH_DEBOUNCE_SECONDS` | `10` | Quiet period before a change triggers a scan |
| `HEARTH_SCAN_MISSING_GRACE_HOURS` | `24` | Grace period before a file that disappeared is removed from the catalog |
| `HEARTH_SCAN_MASS_DELETE_THRESHOLD` | `0.30` | Fraction of a library that may disappear before a scan aborts as a safeguard |
| `HEARTH_SCAN_MASS_DELETE_MIN_FILES` | `10` | Minimum file count before the mass-delete guard applies |

## Metadata

| Variable | Default | Purpose |
| --- | --- | --- |
| `HEARTH_TMDB_API_KEY` | unset | TMDB v3 API key or v4 read token; movie/TV items stay `pending` without it |
| `HEARTH_METADATA_MATCH_THRESHOLD` | `0.80` | Minimum confidence for an automatic match |
| `HEARTH_MUSICBRAINZ_ENABLED` | `true` | Artist and album metadata from MusicBrainz |
| `HEARTH_OPENLIBRARY_ENABLED` | `true` | Primary book metadata provider |
| `HEARTH_GOOGLE_BOOKS_ENABLED` | `true` | Book metadata fallback |
| `HEARTH_GOOGLE_BOOKS_API_KEY` | unset | Optional Google Books API key; raises the anonymous request quota |
| `HEARTH_LISTENBRAINZ_ENABLED` | `true` | ListenBrainz scrobbling support |

Local `.nfo` files, `metadata.opf` files, embedded tags, and sidecar artwork are
always preferred over online providers. Artwork downloads are restricted to an
allowlist (`HEARTH_IMAGE_ALLOWED_HOSTS`) and capped by size; see
`app/core/config.py` for the image and provider timeouts.

The **Admin → Metadata** tab can store TMDB and Google Books API keys in the
database. A value saved there overrides the corresponding `HEARTH_*` variable
and is picked up by the worker on its next scan, so a restart is not required.
Clearing a key in the UI removes it entirely rather than falling back to the
environment. MusicBrainz and Open Library do not require API keys.

## Plugins

| Variable | Default | Purpose |
| --- | --- | --- |
| `HEARTH_PLUGINS_DIR` | `/config/plugins` | Root directory for user-installed custom plugins |
| `HEARTH_BUILTIN_PLUGINS_DIR` | `/app/plugins` (container) or `./plugins` | Root directory for built-in plugins |

## Playback and transcoding

| Variable | Default | Purpose |
| --- | --- | --- |
| `HEARTH_RENDER_DEVICE` | `/dev/dri/renderD128` | VA-API render node |
| `HEARTH_TRANSCODE_FORCE_SOFTWARE` | `false` | Ignore the GPU and always use `libx264` |
| `HEARTH_TRANSCODE_MAX_HW_SESSIONS` | `4` | Concurrent hardware transcode sessions |
| `HEARTH_TRANSCODE_MAX_SW_SESSIONS` | `4` | Concurrent software transcode sessions |
| `HEARTH_TRANSCODE_IDLE_TIMEOUT_SECONDS` | `60` | Stop an idle transcode session after this delay |
| `HEARTH_TRANSCODE_FIRST_SEGMENT_TIMEOUT_SECONDS` | `30` | Max wait time for initial/seek segment production |
| `HEARTH_TRANSCODE_SEGMENT_WAIT_TIMEOUT_SECONDS` | `10` | Bounded wait time for individual HLS segment delivery |
| `HEARTH_TRANSCODE_SEGMENT_DURATION_SECONDS` | `4` | Target segment duration in seconds |
| `HEARTH_WATCHED_THRESHOLD` | `0.90` | Fraction of an item that counts as watched |
| `HEARTH_STREAM_CHUNK_BYTES` | `524288` | Direct-play read size (512 KiB) |

Session caps and the idle timeout can also be adjusted at runtime from
**Admin → Transcoding**. Runtime changes are not written to disk and reset when
the container restarts; set the environment variables to make them permanent.

## Container filesystem

The published image runs as UID/GID 1000 with a read-only root filesystem, all
capabilities dropped, and `no-new-privileges`. Only these paths are writable:

| Path | Mount | Contents |
| --- | --- | --- |
| `/config` | named volume | SQLite database, cached artwork, generated secret, backups |
| `/transcode` | tmpfs | In-progress transcode segments (safe to lose) |
| `/tmp` | tmpfs | Short-lived working files |

Media is mounted read-only. Library paths added in Hearth must resolve inside
`HEARTH_MEDIA_DIR`.

## Related guides

- [Hardware Transcoding](HARDWARE_TRANSCODING.md) — GPU drivers, render GIDs, verification
- [Backup and Upgrade](BACKUP_AND_UPGRADE.md) — what `/config` holds and how to restore it
- [Reverse Proxy](REVERSE_PROXY.md) — TLS, streaming, and SSE through Caddy, Traefik, or Nginx
- [Troubleshooting](TROUBLESHOOTING.md) — permission, playback, and database-lock issues
