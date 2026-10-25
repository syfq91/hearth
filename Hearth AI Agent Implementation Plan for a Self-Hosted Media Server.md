# Hearth: AI Agent Implementation Plan for a Self-Hosted Media Server

> A phased build plan for an AI coding agent · Python/FastAPI · Docker · VAAPI · October 21, 2026

## 0. How the agent should use this document

This plan is written to be executed by an autonomous coding agent working in a git repository. Follow these operating rules throughout.

- Work one phase at a time. Do not start a phase until the previous phase's acceptance criteria all pass. Never skip ahead to "nice to have" features.
- Work in small vertical slices. Each task should end with code, tests and a commit. Commit messages use Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`).
- Tests first for logic, tests alongside for I/O. Pure logic (playback decision engine, filename parsing, ffmpeg argument building) is written test-first. Everything else gets tests in the same commit.
- Keep a living log. Maintain `docs/PROGRESS.md` (phase, task, status, open questions) and `docs/DECISIONS.md` (one short entry per non-obvious decision: context, choice, alternatives). Update both at the end of every task.
- Never invent hardware behavior. VAAPI results must come from running the real probe. If no GPU is available in the environment, build against the interface, use the software fallback, and mark the hardware acceptance items as "needs manual verification" in `PROGRESS.md`. Do not claim they pass.
- Ask or record, do not guess, on ambiguity. If a requirement is unclear, choose the simplest option consistent with this plan, record it in `DECISIONS.md`, and continue.
- Security by default. No secrets in the repo, media mounted read-only, no shell string interpolation for subprocesses (always argument lists), all path inputs validated against library roots.
- Definition of done for every task: lint clean (`ruff`), type check clean (`mypy`), tests green (`pytest`), migrations included if the schema changed, docs updated if behavior changed.

## 1. Fixed decisions and scope

Target: home server, maximum 15 users, a handful of concurrent streams. Single node, no horizontal scaling.

### Fixed stack

| Area | Decision |
| --- | --- |
| Language | Python 3.12+ |
| API framework | FastAPI with Pydantic v2, async endpoints |
| ORM and migrations | SQLAlchemy 2.0 (async) with Alembic |
| Database | SQLite in WAL mode (code kept Postgres-compatible) |
| Search | SQLite FTS5 |
| Background jobs | Own `jobs` table plus asyncio worker (no Redis) |
| Video tooling | ffmpeg and ffprobe, called via `asyncio.create_subprocess_exec` |
| Hardware transcoding | VAAPI only (Intel iGPU or AMD), software x264 fallback |
| Packaging | Docker image on Debian 12 slim, docker compose |
| Package manager | `uv` |
| Quality tools | Ruff, mypy (strict on `app/`), pytest, pytest-asyncio, httpx |
| Frontend | Undecided. Backend is API-first and must not depend on a frontend choice until Phase 9 |

- Media types: movies, TV, music, books.
- Compatibility surfaces: none (native API-first).
- Non-goals: live TV/DVR, DLNA/UPnP, plugins, federation, native apps, NVENC/QSV-specific paths, cloud deployment, user-uploaded media.

## 2. Repository layout and conventions

```text
hearth/
  app/
    main.py                 # FastAPI app factory
    api/v1/                 # routers: auth, users, libraries, items, playback, stream, images, search, admin
    core/                   # config, security, logging, errors, deps
    db/                     # base, models, session, migrations (alembic)
    services/
      scanner/              # walkers, watchers, matchers
      metadata/             # providers: tmdb, musicbrainz, openlibrary, nfo, local
      media/                # ffprobe wrapper, tag readers, book readers
      transcode/            # capability probe, decision engine, ffmpeg builder, session manager
      images/               # fetch, resize, cache
      jobs/                 # queue, worker, handlers
    workers/                # worker entrypoint
  tests/
    unit/ integration/ e2e/
    fixtures/               # generated tiny media, never real copyrighted media
  docker/                   # Dockerfile, compose.yaml, compose.dev.yaml, entrypoint.sh
  docs/                     # PROGRESS.md, DECISIONS.md, ARCHITECTURE.md, API.md
  scripts/                  # dev helpers, fixture generators
  pyproject.toml
```

### Conventions

- Configuration through environment variables parsed by `pydantic-settings`, prefix `HEARTH_`. Defaults must be safe and documented.
- IDs are UUIDv7 (or ULID) stored as text, so they sort by creation time.
- All timestamps are UTC, timezone-aware.
- Errors use one JSON shape: `{"error": {"code": "...", "message": "...", "details": {}}}`.
- Structured JSON logging with a request ID on every line.
- API and worker run from the same image with different commands: `hearth api` and `hearth worker` (a small Typer CLI).

## 3. Cross-cutting test strategy

- Fixtures are generated, not downloaded. `scripts/make_fixtures.py` uses ffmpeg to create 2-second test videos (H.264, HEVC, 10-bit HDR-flagged, with multiple audio tracks, SRT and PGS-like subtitle variants), short audio files (FLAC, MP3, Opus with tags), and minimal EPUB/CBZ/PDF files.
- Test layers: unit (pure logic), integration (API with a temp SQLite DB and fixture library), e2e (Docker compose up, scripted client).
- Hardware tests are marked `@pytest.mark.vaapi` and skipped when `/dev/dri/renderD128` is absent.
- Coverage target: 80% on `services/`, with 100% of branches in the decision engine and ffmpeg argument builder covered.
- CI (GitHub Actions): lint, type check, unit and integration tests, Docker build. Hardware tests are run manually on the real server.

---

## Phase 0: Bootstrap and tooling

**Goal:** an empty but fully wired project that builds, lints, tests and runs in Docker.

### Tasks

- [ ] Initialize the repo with `uv`, `pyproject.toml`, Ruff and mypy config, pytest config, `.gitignore`, `.editorconfig`, pre-commit hooks.
- [ ] Create the package layout from section 2 with placeholder modules.
- [ ] Implement `app/main.py` with an app factory, `/api/v1/health` (returns version, DB status, ffmpeg presence) and `/api/v1/version`.
- [ ] Implement `core/config.py` (pydantic-settings), `core/logging.py` (JSON logs, request IDs) and the unified error handler.
- [ ] Add the Typer CLI with `api`, `worker`, `migrate` and `create-admin` commands (stubs allowed for the last two).
- [ ] Write the Dockerfile (multi-stage, non-root user, `tini` as init, ffmpeg plus VAAPI drivers `intel-media-va-driver-non-free`, `mesa-va-drivers`, `vainfo`, `libva-drm2`). Write `compose.yaml` with the `/dev/dri` device, `group_add` render GID, read-only media mounts, tmpfs for `/transcode`, and `/config` volume.
- [ ] Add GitHub Actions workflow.
- [ ] Write `docs/ARCHITECTURE.md` (a copy of the section 2 layout plus a module diagram) and the first `PROGRESS.md`.

**Deliverables:** green CI, `docker compose up` serves `/api/v1/health`.

### Acceptance criteria

- [ ] `uv run pytest`, `ruff check`, `mypy app` all pass.
- [ ] The container runs as a non-root user and the health endpoint reports ffmpeg version.
- [ ] Inside the container, `vainfo` runs (on a GPU host it lists profiles; elsewhere it fails gracefully and the health endpoint reports `vaapi: unavailable`).

> **Pitfalls:** the render group GID differs per host, so make it a documented compose variable. Use Debian, not Alpine, for driver compatibility.

---

## Phase 1: Foundation: database, users and auth

**Goal:** a secure multi-user API skeleton that every later phase builds on.

### Tasks

- [ ] SQLAlchemy base, async session, SQLite pragmas on connect (`journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`). Alembic set up with an initial migration. Migrations run automatically at API startup behind a lock.
- [ ] Models: `User`, `RefreshToken`, `Library`, `LibraryPath`, `LibraryAccess`, `Setting`, `Job`.
- [ ] Password hashing with argon2 (`argon2-cffi`). Password policy: minimum 8 characters.
- [ ] Auth endpoints under `/api/v1/auth`: first-run setup (only allowed when zero users exist), login, refresh (rotating refresh tokens with reuse detection), logout, and `me`.
- [ ] Short-lived JWT access tokens (15 minutes). Refresh tokens stored hashed.
- [ ] Media access tokens: an endpoint that mints short-lived signed tokens (HMAC, 6-hour default, scoped to a user and optionally an item) for use in `<video>`, `<img>` and `<audio>` query strings, since those cannot send headers.
- [ ] Roles: `admin` and `user`. Dependency helpers `require_user`, `require_admin`, `require_library_access(library_id)`.
- [ ] User management (admin): create, edit, disable, reset password, set per-user options (max streaming bitrate, transcode allowed, library access). Enforce a configurable maximum of 15 enabled users.
- [ ] Library CRUD (admin): name, type (`movie`, `tv`, `music`, `book`), one or more root paths. Validate that paths exist, are readable and are inside the configured media mount.
- [ ] Login rate limiting (per IP and per username) with an in-memory sliding window. Audit log table for admin actions.
- [ ] Jobs framework: `Job` table (type, payload, status, progress, error, timestamps), an asyncio worker loop with a polling claim query, concurrency limits per job type, retry with backoff, and cancellation. Progress events exposed over SSE at `/api/v1/events`.

**Deliverables:** working auth, user and library management, job runner with a no-op test job.

### Acceptance criteria

- [ ] Setup endpoint returns 409 once an admin exists.
- [ ] A disabled user's tokens stop working immediately.
- [ ] A user without library access receives 404 (not 403) for that library's items.
- [ ] Reusing an already-rotated refresh token revokes the whole token family.
- [ ] Job survives a worker restart (re-queued if it was running).
- [ ] OpenAPI schema is generated and committed to `docs/openapi.json` by a script.

> **Pitfalls:** SQLite allows one writer, so keep write transactions short and never hold a transaction open across an `await` on network or subprocess I/O.

---

## Phase 2: Media probing, scanner and movie metadata

**Goal:** movie libraries are discovered, probed, matched to TMDB and browsable through the API.

### Tasks

- [ ] ffprobe wrapper (`services/media/probe.py`): runs `ffprobe -print_format json -show_format -show_streams -show_chapters` with a timeout, returns a typed model (container, duration, bitrate, video/audio/subtitle streams with codec, profile, level, pixel format, HDR flags, channel layout, language, default and forced flags). Store the result as JSON on `MediaFile`.
- [ ] Models: `MediaItem` base with type-specific tables for `Movie`, plus `MediaFile`, `Image`, `Person`, `Genre`, `ItemPerson`, `ItemGenre`, `ProviderCache`. Add FTS5 virtual table for titles with triggers or application-level sync.
- [ ] Scanner (`services/scanner`): walk library roots with `os.scandir` in a thread executor, filter by extension and ignore patterns (`.nomedia`, `sample`, `extras`), compare path, size and mtime to detect new, changed and removed files, and probe only what changed. Removed files are soft-deleted for a grace period, so a temporarily unmounted share does not wipe the library. Add a safeguard: if more than 30% of a library vanishes in one scan, abort and flag it.
- [ ] Movie filename parsing with `guessit` (title, year, edition, part). Folder-based hints (`Title (Year)/Title (Year).mkv`). Multiple files for one movie become multiple versions.
- [ ] Metadata provider interface (`services/metadata/base.py`): `search`, `fetch_details`, `fetch_images`, with typed results. Implement `TMDBProvider` (API key from config, respect rate limits, cache responses in `ProviderCache`) and `LocalProvider` (reads `.nfo` Kodi-style XML, plus `poster.jpg`, `fanart.jpg`, and sidecar subtitles).
- [ ] Matching: score candidates by normalized title similarity, year and runtime, auto-accept above a threshold, otherwise mark the item `unmatched`. Local NFO IDs always win. Provide a manual match endpoint: `POST /api/v1/items/{id}/match` with a provider ID, plus a search endpoint for the admin UI.
- [ ] Image pipeline: download posters and backdrops, store originals in `/config/cache/images`, generate resized variants on demand (Pillow in a process pool) keyed by width and format (WebP/JPEG), plus BlurHash for placeholders.
- [ ] Scheduling: scan job triggered manually, on a schedule (default every 6 hours) and via `watchfiles` for local filesystems. Watcher events are debounced (10 seconds) and mapped to a targeted path scan.
- [ ] Read API: `GET /api/v1/libraries/{id}/items` (cursor pagination, sort by title, added date, year, rating; filter by genre, year, watched state), `GET /api/v1/items/{id}`, `GET /api/v1/images/{id}`.

**Deliverables:** pointing the server at a movie folder yields a populated, searchable catalog with artwork.

### Acceptance criteria

- [ ] Re-scanning an unchanged library performs zero ffprobe calls and zero provider calls.
- [ ] Renaming a file is detected as a move (partial hash match on size plus first and last 64 KB) and preserves watch state.
- [ ] Provider failures never fail a scan (item stays pending and is retried later).
- [ ] A fixture library of 50 generated files scans in under 30 seconds on CI.
- [ ] Unit tests cover filename parsing edge cases (years in titles, multi-part, editions).

> **Pitfalls:** TMDB terms require attribution, so add the logo and notice to docs and the About endpoint. Do not run ffprobe on network mounts without a timeout and a concurrency cap (default 4).

---

## Phase 3: Direct play, progress tracking and a test player

**Goal:** a user can play a movie as-is, and the server remembers where they stopped.

### Tasks

- [ ] `GET /api/v1/stream/{file_id}` serving the original file with full HTTP range support (206 responses, `Accept-Ranges`, correct `Content-Type`, `ETag`/`Last-Modified`), authenticated by bearer token or media access token. Use async file streaming with a sane chunk size (256 KB to 1 MB). Reject any path not registered in `MediaFile`.
- [ ] `UserItemState` model and endpoints: report progress (`POST /api/v1/items/{id}/progress` with position and state), mark watched or unwatched, favorite, rating. Auto-mark watched at 90% progress or when credits chapters are reached.
- [ ] Home endpoints: `continue-watching`, `recently-added` per library, `favorites`.
- [ ] External subtitle discovery (`.srt`, `.ass`, `.vtt` next to the video) and `GET /api/v1/subtitles/{id}.vtt` converting to WebVTT.
- [ ] Minimal throwaway test page (`tools/testplayer/index.html`, a single static file, not the real frontend) that logs in, lists items and plays one with the native `<video>` element. Clearly labeled as a developer tool.
- [ ] Playback info endpoint `POST /api/v1/items/{id}/playback-info`. In this phase it only returns `direct_play` with a stream URL, but define the final request and response schema now (client capability profile in, a decision plus URLs and reasons out), so Phase 7 (transcoding) does not break clients.

### Acceptance criteria

- [ ] Seeking in the test player works (range requests verified with an automated test using `Range: bytes=...` headers, including open-ended and suffix ranges).
- [ ] Progress is per user and isolated between users.
- [ ] Streaming 5 concurrent files does not block API responses (event loop stays responsive; add a latency test).

---

## Phase 4: TV shows

**Goal:** series, seasons and episodes with correct matching and "next up".

### Tasks

- [ ] Models: `Series`, `Season`, `Episode` (with season and episode numbers, air date, absolute number optional), linked to `MediaFile`.
- [ ] Parsing with `guessit`, including multi-episode files (`S01E01E02`), daily shows (date-based), specials (season 0), and anime-style absolute numbering as best-effort.
- [ ] `TMDBProvider` TV support: series, season and episode details, episode stills, cast. Handle TMDB episode groups only if time allows (record as a decision if skipped).
- [ ] Scanner changes: group files by series folder, match the series first, then episodes by number. Unmatched episodes are listed under the series as "unknown".
- [ ] API: series detail, seasons list, episodes list, next-up (first unwatched episode after the last watched), continue watching merged with movies, mark season or series watched.
- [ ] Playback uses the Phase 3 direct play path unchanged. Verify no regressions.
- [ ] Local NFO support for `tvshow.nfo` and `episode.nfo`.

### Acceptance criteria

- [ ] A fixture tree with 3 series covering normal, multi-episode, date-based and specials files matches 100% correctly.
- [ ] Next-up behaves correctly across season boundaries and with out-of-order watching.
- [ ] Manual re-match of a series moves all episodes and preserves watch states where episode numbers still exist.

---

## Phase 5: Music

**Goal:** music libraries can be scanned, enriched, browsed, and played via the native API with play tracking.

### Tasks

- [ ] Models: `Artist`, `Album`, `Track`, plus `Playlist` and `PlaylistItem`. Track fields include disc and track number, duration, bitrate, sample rate, ReplayGain, MusicBrainz IDs.
- [ ] Tag reading with `mutagen` (ID3, Vorbis, MP4, APE). Embedded cover art extraction. Tags take priority over folder structure, with folder fallback. Handle compilations ("Various Artists"), multi-disc albums and multiple artists (album artist vs track artist).
- [ ] Metadata enrichment (optional and rate-limited): MusicBrainz lookups by MBID or tags, Cover Art Archive for missing covers, artist images from local `artist.jpg` first. Respect the 1 request per second limit and set a proper User-Agent.
- [ ] Native API: artists, albums, tracks, album detail, artist detail, search (FTS5), random, recently added, most played, playlists CRUD.
- [ ] Audio streaming and playback: direct audio streaming endpoints with gapless-friendly behavior: accurate durations, correct content-length, and HTTP range support.
- [ ] Play tracking: play counts, last played, favorites, ratings, and optional ListenBrainz scrobbling (per-user token, queue with retry).

### Acceptance criteria

- [ ] Native music API endpoints (browsing, search, playlist management, audio streaming, play tracking) pass automated tests against a fixture library.
- [ ] Audio streaming supports range requests and returns accurate durations and content lengths.
- [ ] A 5,000-track generated library scans in under 2 minutes and album list queries return in under 200 ms.

---

## Phase 6: Books

**Goal:** ebooks and comics are catalogued, downloadable, and readable with progress sync.

### Tasks

- [ ] Models: `Author`, `Book`, `BookSeries`, with `MediaFile` per format. Formats: EPUB, PDF, CBZ, CBR (via `rarfile` or `unrar`, optional), and MOBI/AZW3 as download-only (optional conversion via Calibre CLI is out of scope for the initial build).
- [ ] Readers: EPUB metadata from OPF using `ebooklib` or direct zip and XML parsing, cover extraction, series metadata (`calibre:series`), PDF metadata via `pypdf` with first-page cover rendering (`pdfium` or `pymupdf`, record licensing choice in `DECISIONS.md`), and comic metadata from `ComicInfo.xml` plus the first image as cover.
- [ ] Provider: Open Library (search by ISBN or title and author) with Google Books as a fallback. Local OPF and `cover.jpg` always win.
- [ ] Native API: books, authors, series, search, download (`/api/v1/books/{id}/file/{format}`), and per-book reading progress (`locator` string, percentage, device name, timestamp, last write wins).
- [ ] Comic page serving: `GET /api/v1/books/{id}/pages/{n}` extracting the nth image from CBZ/CBR with caching and resizing, so web readers do not download whole archives.
- [ ] Reader integration notes for Phase 9 (epub.js or foliate-js for EPUB, pdf.js for PDF).

### Acceptance criteria

- [ ] Fixture EPUB, PDF and CBZ files produce correct title, author, series and cover.
- [ ] Native book API endpoints (browsing, search, download, comic page extraction, reading progress) pass automated tests against fixture files.
- [ ] Progress written from one client is returned to another.

---

## Phase 7: Transcoding with VAAPI

**Goal:** any file plays on any reasonable client, using the GPU when available.

> This is the highest-risk phase. Build it in the order below and keep each step independently testable.

### Tasks

- [ ] Capability probe (`services/transcode/capabilities.py`): at startup and on demand, detect the render device, run `vainfo`, parse supported profiles and entrypoints (decode and encode per codec), then verify with a real 1-second test encode per codec (H.264, HEVC, AV1 if present). Cache results and expose them at `GET /api/v1/admin/transcoding/capabilities`. Provide an override setting to force software mode.
- [ ] Client capability profile (Pydantic model): supported containers, video codecs and profiles, audio codecs, max resolution, max bitrate, subtitle formats, HDR support, and an HLS-fMP4 flag. Ship built-in presets (generic Chrome, Firefox, Safari, Android TV, a conservative "unknown client"), and let the client override them.
- [ ] Decision engine (`decision.py`, pure function, fully unit tested): inputs are the file's streams, the client profile, user limits and server capabilities. Output is a plan with: video action (`copy` or `transcode`), audio action (`copy` or `transcode` plus target), subtitle action (`none`, `sidecar_vtt`, `burn_in`), container or delivery (`direct`, `remux_hls`, `transcode_hls`), target resolution and bitrate, and a human-readable list of reasons. Include a table-driven test matrix of at least 40 cases.
- [ ] ffmpeg command builder (`builder.py`, pure function, snapshot tested): produces argument lists for these pipelines and nothing else.
  - Full VAAPI: `-hwaccel vaapi -hwaccel_output_format vaapi -vaapi_device <dev>`, `scale_vaapi`, `h264_vaapi` or `hevc_vaapi`, constant-quality or bitrate-capped rate control.
  - HDR to SDR: `tonemap_vaapi` when supported, otherwise a documented fallback (hwdownload plus software `tonemap`/`zscale`, or refuse HDR transcode with a clear reason).
  - Burn-in subtitles: software overlay path via `hwdownload`, `overlay`, `hwupload`.
  - Software x264 fallback (`libx264`, preset `veryfast`).
  - Audio to AAC with stereo downmix, and audio-only remux paths.
- [ ] Session manager (`session.py`): one ffmpeg process per playback session, output to `/transcode/<session_id>/` as HLS fMP4 (`-hls_segment_type fmp4`, 4-second segments, event/VOD playlist). Features: start-at offset (seek restarts ffmpeg with `-ss` before `-i`), idle timeout (default 60 seconds without segment requests), cleanup on end plus a startup sweep, stderr parsing for progress, crash detection with automatic fallback from hardware to software on the first failure, and a global concurrency cap with an explicit `503` and a `Retry-After` when exceeded (separate caps for hardware and software sessions).
- [ ] HLS endpoints: `/api/v1/hls/{session}/master.m3u8`, `/media.m3u8`, `/init.mp4`, `/seg_N.m4s`, authenticated by media token. Delay the playlist response until the first segment exists. Session stop endpoint, and sessions reaped when the user's token is revoked.
- [ ] Audio and track selection: the client chooses audio and subtitle track indexes, and the plan is regenerated on change.
- [ ] Music transcoding path: FLAC to Opus/MP3/AAC at a requested bitrate, streamed directly (no HLS).
- [ ] Test player upgrade: add `hls.js`, a codec profile selector, and a debug panel showing the plan and ffmpeg command.
- [ ] Admin diagnostics: list active sessions (user, item, mode, hardware or software, fps, speed), kill a session, and view the last 200 lines of ffmpeg stderr.

### Acceptance criteria

- [ ] Decision engine matrix passes. Builder snapshot tests pass for every pipeline.
- [ ] On a machine with VAAPI: a 4K HEVC HDR fixture transcodes to 1080p H.264 SDR at faster than 1.0x speed, and `intel_gpu_top` (or equivalent) shows GPU use. Record results in `docs/HARDWARE_TESTS.md`.
- [ ] On a machine without VAAPI: the same file plays through the software path.
- [ ] Seeking mid-stream works, killing the browser tab ends ffmpeg within the idle timeout, and no orphan ffmpeg processes remain after 20 start/stop cycles (automated test).
- [ ] Exceeding the concurrency cap returns a clean error, not a hang.

> **Pitfalls:** never pass user-controlled strings to a shell. Hardware filter chains differ by GPU generation, so every pipeline needs the software fallback. Keep `/transcode` on tmpfs sized for peak sessions (4 GB default). Some clients choke on `-ss` accuracy, so test seeking on keyframes and with `-copyts` handling.

---

## Phase 8: Search, collections, admin and API polish

**Goal:** make the API complete enough that a frontend is straightforward to build.

### Tasks

- [ ] Unified search `GET /api/v1/search?q=` across all media types using FTS5 with prefix matching, ranking, and grouping by type. Respect per-user library access.
- [ ] Collections and playlists: admin-curated collections (for example a franchise auto-collected from TMDB collection IDs) and per-user playlists across video and music.
- [ ] Content controls: per-user maximum rating (TMDB certification mapping) and per-library access (already built; add tests across all endpoints).
- [ ] Admin API: library scan controls, unmatched items list, manual fix tools, job history, log tail, system info, transcode settings (hardware enable, concurrency caps, default bitrate caps), backup and restore.
- [ ] Backups: `hearth backup` CLI and an admin endpoint that creates a consistent SQLite snapshot (use the SQLite backup API), plus settings export. Restore command with version check.
- [ ] Real-time events: extend SSE with scan progress, library updated, and now playing (admin view).
- [ ] API hardening pass: consistent pagination, field selection, ETags on item detail, input validation limits, CORS configuration, request size limits, and OpenAPI examples.
- [ ] Generate the typed API client artifact (`openapi-typescript` or `openapi-python-client`) as a build step, ready for Phase 9.
- [ ] Write `docs/API.md` with authentication flows and the playback-info contract.

### Acceptance criteria

- [ ] An authorization test suite proves every endpoint enforces login, role and library access (parametrized over the full OpenAPI route list, which fails if a route is added without a declared policy).
- [ ] Backup then restore into a fresh container yields an identical catalog.
- [ ] The OpenAPI schema has no untyped (`Any`) responses on public endpoints.

---

## Phase 9: Frontend selection and build

**Goal:** choose a frontend, then build the first usable UI against the stable API.

### Step 1: Decision (the agent produces a recommendation, the human decides).

Write `docs/FRONTEND_DECISION.md` comparing at least three options (for example React with Vite, SvelteKit as an SPA, Vue with Vite) against these criteria: HLS playback support, ability to handle D-pad and remote navigation on TV browsers, ecosystem for EPUB and PDF readers, bundle size, the typed client generation story, and long-term maintainability for a small project. End with a recommendation and stop and wait for approval before building.

### Step 2: Build (after approval).

- [ ] Scaffold in `web/` using the generated API client. The static build is served by FastAPI at `/` (or by the reverse proxy).
- [ ] Screens, in order: setup and login, home (continue watching, recently added, next up), library grid with filters, movie and series detail, video player (hls.js, track selection, subtitles, resume, next episode, quality selector, playback reporting), music (artists, albums, queue player with gapless attempts and the Media Session API), books (grid, EPUB, PDF and comic reader with synced progress), search, user profile, admin (users, libraries, scan, unmatched fixer, transcoding status).
- [ ] Responsive layout for phone, tablet and desktop, plus a focus-managed 10-foot mode for TV browsers.
- [ ] Accessibility basics: keyboard navigation, focus rings, captions, reduced motion.
- [ ] Playwright e2e tests: login, play a fixture video, seek, resume, read a fixture EPUB, play a track.

### Acceptance criteria

- [ ] All Playwright flows pass in CI against the Docker image.
- [ ] A first-time user can go from `docker compose up` to watching a video using only the UI.
- [ ] Lighthouse performance score of at least 80 on the home page on a desktop profile.

---

## Phase 10: Hardening, performance and release

**Goal:** a trustworthy 1.0 that a household can rely on.

### Tasks

- [ ] Load test with a scripted simulation of 15 users: 3 concurrent transcodes, 6 direct plays, a background scan, and API browsing. Targets: API p95 under 300 ms, no dropped segments, flat memory growth over 2 hours. Record the results.
- [ ] Resilience: test server restart during playback and scan, a missing or unmounted library path, a full disk for cache, a corrupted media file (an ffprobe failure is recorded and the file is skipped, not retried forever), and SQLite lock contention.
- [ ] Security review: dependency audit (`pip-audit`), path traversal tests, token scope tests, SSRF review of any URL fetching (image downloads restricted to known provider hosts), default-deny CORS, security headers, secrets never logged.
- [ ] Observability: optional Prometheus `/metrics` endpoint (off by default), structured logs, an admin log viewer, and clear error messages when ffmpeg or VAAPI fails.
- [ ] Documentation: README quick start, compose examples for Intel and AMD, a hardware transcoding guide (finding the render GID, verifying with `vainfo`, common errors), reverse proxy examples (Caddy and Traefik with TLS), backup and upgrade guide, and a troubleshooting FAQ.
- [ ] Release engineering: semantic versioning, multi-arch image build (amd64 at minimum), published image with a tagged release, changelog, and a migration compatibility test from the previous release.
- [ ] Final acceptance run: fresh install on the real server, a real library of each media type, and a full manual checklist stored in `docs/RELEASE_CHECKLIST.md`.

### Acceptance criteria

- [ ] Every item in `RELEASE_CHECKLIST.md` is checked off, with any hardware-dependent items verified by a human.
- [ ] No open high-severity issues in `PROGRESS.md`.

---

## Appendix A: Core data model summary

| Table | Key fields |
| --- | --- |
| `users` | id, username, password_hash, role, disabled, max_bitrate, allow_transcode, max_rating |
| `refresh_tokens` | id, user_id, family_id, token_hash, expires_at, revoked_at |
| `libraries`, `library_paths`, `library_access` | type, name, root paths, user-to-library grants |
| `media_items` | id, type, title, sort_title, year, overview, library_id, added_at, match_state, provider_ids |
| `movies`, `series`, `seasons`, `episodes` | type-specific fields and parent links |
| `artists`, `albums`, `tracks` | tags, MusicBrainz IDs, disc and track numbers |
| `authors`, `books`, `book_series` | formats, series index, identifiers |
| `media_files` | item_id, path, size, mtime, partial_hash, container, streams_json, missing_since |
| `images` | item_id, kind, source_url, local_path, blurhash |
| `user_item_state` | user_id, item_id, played, position_ms, favorite, rating, play_count, last_played_at |
| `playlists`, `playlist_items`, `collections` | ordering and ownership |
| `provider_cache` | provider, key, payload, fetched_at, ttl |
| `jobs` | type, payload, status, progress, error, attempts, run_after |
| `audit_log` | actor, action, target, timestamp |

---

## Appendix B: Reference ffmpeg recipes

The agent must treat these as starting points to validate on real hardware, not as guaranteed-correct commands.

```bash
# Full VAAPI H.264 transcode with GPU decode and scale
ffmpeg -hwaccel vaapi -hwaccel_output_format vaapi -vaapi_device /dev/dri/renderD128 \
  -i INPUT -vf "scale_vaapi=w=1920:h=-2:format=nv12" \
  -c:v h264_vaapi -b:v 6M -maxrate 8M -bufsize 12M \
  -c:a aac -ac 2 -b:a 192k \
  -f hls -hls_segment_type fmp4 -hls_time 4 OUTPUT/media.m3u8

# Software fallback
ffmpeg -i INPUT -vf "scale=1920:-2" -c:v libx264 -preset veryfast -crf 23 \
  -c:a aac -ac 2 -f hls -hls_segment_type fmp4 -hls_time 4 OUTPUT/media.m3u8

# Capability probe encode (1 second, null output)
ffmpeg -init_hw_device vaapi=va:/dev/dri/renderD128 -f lavfi -i testsrc=d=1:s=1280x720 \
  -vf "format=nv12,hwupload" -c:v h264_vaapi -f null -
```

---

## Appendix C: Risk register

| Risk | Mitigation |
| --- | --- |
| VAAPI behavior differs per GPU generation | Probe with real test encodes, always keep a software fallback, record results per GPU in `HARDWARE_TESTS.md` |
| Bad metadata matches frustrate users | Local sidecar override, confidence thresholds, manual match tools from Phase 2 |
| Client codec diversity | Table-driven decision engine, conservative default profile, debug panel in the test player |
| SQLite write contention | Short transactions, WAL, single-writer pattern for scans |
| Network mounts are slow or flaky | Timeouts, concurrency caps, mass-deletion safeguard, scheduled rescans in addition to inotify |
| Scope creep across four media types | Strict phase gates; native API keeps all media types focused and consistent |
| Licensing (PDF rendering libraries, provider terms) | Record each dependency choice in `DECISIONS.md` and include attribution in the About endpoint |
