# Hearth

**Your media, your server.** Hearth turns the folders of movies, TV shows,
music, books, and comics on your own hardware into a fast, good-looking web app
for the whole household. Your files stay on your hardware, and there is no
subscription.

It runs as a single Docker container, works on a spare PC, NAS, or home server,
and falls back to CPU transcoding when there is no GPU.

## What you get

- **Movies and TV** — a modern player with resume, subtitle and audio-track
  selection, next-episode prompts, and high-quality SSA/ASS subtitle rendering.
- **Music** — queue playback with album artwork and optional
  [ListenBrainz](https://listenbrainz.org/) scrobbling from your profile.
- **Books and comics** — read EPUB, PDF, and CBZ/CBR in the browser. Reading
  position syncs between your devices.
- **A living-room mode** — a 10-foot TV interface with D-pad and arrow-key
  navigation for smart TVs and media PCs.
- **A place for everyone** — separate accounts, per-user watch and reading
  progress, favorites, and per-library access.
- **Unified search** — find anything across every library at once.
- **Simple administration** — add libraries, scan, fix unmatched metadata,
  and watch active transcodes and jobs from the admin area.

The homepage keeps your unfinished media one click away with **Continue
watching** and **Continue reading** rows.

## Quick start

You need Docker with Compose v2 and a folder containing your media.

1. **Point Hearth at your media.** Copy the example environment file and set
   `MEDIA_DIR` to your library folder:

   ```bash
   cp .env.example .env
   # edit .env: MEDIA_DIR=/path/to/your/media
   ```

2. **Start it.**

   ```bash
   docker compose --env-file .env -f docker/compose.yaml up -d
   ```

3. **Open `http://localhost:8000`.** The setup wizard asks you to create the
   first administrator account. There are no default credentials.

> **No GPU?** Comment out the `devices` and `group_add` blocks in
> `docker/compose.yaml` before starting, or Docker will fail to find the render
> device. Hearth then transcodes on the CPU automatically.

> **Using a GPU?** Intel QuickSync works out of the box with the default
> compose configuration — see [Hardware Transcoding](docs/HARDWARE_TRANSCODING.md).

## Add your library

1. Sign in and open **Admin → Libraries**.
2. Choose **Add library**, pick a type (Movies, TV shows, Music, or Books) and
   a folder inside `/media`, then save.
3. Press **Scan** and follow progress on the admin overview.

Hearth understands common file and folder naming, embedded tags, and local
`.nfo` / `metadata.opf` files plus sidecar artwork. Online metadata is a
fallback: music comes from MusicBrainz, books from Open Library and Google
Books, and movies and TV from TMDB. TMDB needs a free API key — add
`HEARTH_TMDB_API_KEY` to your `.env` before scanning if you want movie and TV
artwork and descriptions. See [Configuration](docs/CONFIGURATION.md) for the
exact variables.

## Everyday use

- The **player** remembers where you stopped on every device and offers
  alternate audio tracks and subtitles.
- **Books** reopen at your last page or comic frame, even if you switch devices.
- **TV mode** gives a remote-friendly layout; turn it on from the account menu.
- **Search** matches titles and cast names across every library at once.
- **Favorites** are per user and appear as a shelf on the home screen.

## Hardware acceleration

Hearth probes the GPU at startup and uses VA-API when available:

- **Intel** (8th Gen and newer QuickSync) works with the default Compose configuration.
- **CPU-only** or non-Intel hosts need no configuration beyond removing the device mapping;
  software `libx264` is the automatic fallback.

Drivers, permissions, render GIDs, and verification commands are covered in the
[Hardware Transcoding Guide](docs/HARDWARE_TRANSCODING.md).

## Running it long-term

- **Update:** `docker compose -f docker/compose.yaml pull` then
  `docker compose -f docker/compose.yaml up -d`. Migrations run automatically.
- **Back up:** use the `hearth backup` command or
  `POST /api/v1/admin/backup`; see
  [Backup and Upgrade](docs/BACKUP_AND_UPGRADE.md). Back up `/config`, which
  holds the database and signing secret.
- **Put it behind TLS:** ready-made Caddy, Traefik, and Nginx examples are in
  [Reverse Proxy](docs/REVERSE_PROXY.md).
- **Something broken?** Start with [Troubleshooting](docs/TROUBLESHOOTING.md).

## Documentation

| Guide | What it covers |
| --- | --- |
| [Configuration](docs/CONFIGURATION.md) | Environment variables, secrets, data locations |
| [Hardware Transcoding](docs/HARDWARE_TRANSCODING.md) | GPU setup, VAAPI drivers, render GIDs |
| [Backup and Upgrade](docs/BACKUP_AND_UPGRADE.md) | Backups, restores, upgrades, rollback |
| [Reverse Proxy](docs/REVERSE_PROXY.md) | TLS and streaming through Caddy, Traefik, Nginx |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Playback, scanning, permissions, database locks |
| [API Reference](docs/API.md) | REST API, authentication, playback contract, examples |
| [Development](docs/DEVELOPMENT.md) | Running from source, tests, generated artifacts |
| [Architecture](docs/ARCHITECTURE.md) | Runtime topology and module layout |
| [Decisions](docs/DECISIONS.md) | Architecture decision records |
| [Changelog](CHANGELOG.md) | Release history |
| [Attribution](docs/ATTRIBUTION.md) | TMDB and third-party notices |

## Development

Source setup, the test and lint commands, and how to regenerate the OpenAPI
schema are in the [Development Guide](docs/DEVELOPMENT.md). The short version:
`uv sync`, `uv run hearth api --reload`, and `uv run pytest`.

## License

MIT. See [LICENSE](LICENSE).
