# Hearth Implementation Progress

## Status Overview

- **Current Phase**: Phase 7 (Transcoding with VAAPI) in progress
- **Overall Status**: Phase 7 in progress

---

## Phase 0: Bootstrap and tooling

**Goal:** An empty but fully wired project that builds, lints, tests, and runs in Docker.

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 0.1 | Initialize repo with `uv`, `pyproject.toml`, Ruff, Mypy, Pytest, `.gitignore`, `.editorconfig`, pre-commit hooks | Complete | `chore: initialize repo, uv tooling, and project configurations`; hooks added in `ci: add lint, type, test, and container smoke workflow` |
| 0.2 | Create package layout with placeholder modules | Complete | `chore: create package layout and placeholder modules` |
| 0.3 | Implement `core/config.py`, `core/logging.py`, and unified error handler | Complete | `feat: add settings, JSON logging, and unified error handling` |
| 0.4 | Implement `app/main.py` with app factory, `/api/v1/health` and `/api/v1/version` | Complete | `feat: add app factory, health/version endpoints, and request IDs` |
| 0.5 | Implement Typer CLI (`api`, `worker`, `migrate`, `create-admin`) | Complete | `feat: add Typer CLI entrypoints` |
| 0.6 | Dockerfile (multi-stage, non-root, tini, VAAPI) and `compose.yaml` | Complete | `feat: add multi-stage Docker image and compose stack` |
| 0.7 | GitHub Actions CI workflow | Complete | `ci: add lint, type, test, and container smoke workflow` |
| 0.8 | Write `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, and finalize Phase 0 | Complete | `docs: add architecture overview and Phase 0 acceptance results` |

### Phase 0 Acceptance Checklist

- [x] `uv run pytest`, `ruff check`, `mypy app` all pass.
  - `pytest`: 38 passed.
  - `ruff check`: clean; `ruff format --check`: clean.
  - `mypy app`: no issues in 24 source files.
- [x] Container runs as non-root user and health endpoint reports ffmpeg version.
  - `id` inside the container: `uid=1000(hearth) gid=1000(hearth) groups=1000(hearth),105`.
  - Root filesystem read-only, all capabilities dropped, `no-new-privileges` set.
  - `/api/v1/health` reports `ffmpeg.version = 5.1.9-0+deb12u1`.
- [x] Inside the container, `vainfo` runs (or reports `vaapi: unavailable` on a non-GPU host).
  - On this GPU host, `vainfo` lists profiles (Intel iHD driver 23.1.1) and health reports `vaapi.status = ok`.
  - With `docker/compose.cpu.yaml` (no `/dev/dri`), health reports `vaapi.status = unavailable`
    with detail `render device not present`, and overall health stays `ok`.

### Deliverables

- Green local quality gates and CI workflow (`.github/workflows/ci.yml`) covering lint,
  formatting, strict typing, tests, wheel build and a container health smoke test.
- `docker compose up` serves `/api/v1/health` from the built image.
- Architecture and decision records live in `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`.

### Known Phase 0 Limitations (intentional)

- Database health is file-level only; SQLAlchemy, migrations and real DB status arrive in Phase 1.
- `hearth worker` idles and `hearth migrate` / `hearth create-admin` are stubs
  (`create-admin` exits non-zero so it cannot be mistaken for success).
- Only `/api/v1/health` and `/api/v1/version` are exposed; authentication, media
  libraries and all media features start in Phase 1.
- Media access tokens, HLS, and the deeper VAAPI capability probe are Phase 3/4 work.

---

## Phase 1: Foundation: database, users and auth

**Goal:** A secure multi-user API skeleton that later phases build on.

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 1.1 | Async SQLAlchemy base/session, SQLite pragmas, Alembic baseline, locked startup migrations | Complete | `feat: add async database foundation and locked migrations` |
| 1.2 | Models: `User`, `RefreshToken`, `Library`, `LibraryPath`, `LibraryAccess`, `Setting`, `Job`, `AuditLog` | Complete | `feat: add phase 1 domain models and migration` |
| 1.3 | Argon2 password hashing and password policy | Complete | `feat: add argon2 password hashing and credential policy` |
| 1.4 | Auth endpoints: setup, login, refresh rotation/reuse detection, logout, me | Complete | `feat: add setup, login, refresh, logout and me endpoints` |
| 1.5 | Short-lived JWT access tokens and hashed refresh tokens | Complete | `test: cover access-token claims and refresh-token hashing` |
| 1.6 | Media access tokens (HMAC, scoped, for query-string consumers) | Complete | `feat: add scoped HMAC media access tokens` |
| 1.7 | Roles and dependencies (`require_user`, `require_admin`, `require_library_access`) | Complete | `feat: add role and library-access dependencies` |
| 1.8 | Admin user management and 15-enabled-user limit | Complete | `feat: add admin user management and enabled-user limit` |
| 1.9 | Admin library CRUD with media-root path validation | Complete | `feat: add library CRUD with media-root containment` |
| 1.10 | Login rate limiting and admin audit log | Complete | `feat: add login throttling and admin audit access` |
| 1.11 | Jobs table, asyncio worker, retries, cancellation and SSE events | Complete | `feat: add durable job worker, job API and SSE events` |
| 1.12 | Generate and commit `docs/openapi.json` | Complete | `chore: export and verify the OpenAPI schema` |

### Phase 1 Acceptance Checklist

- [x] Setup endpoint returns 409 once an admin exists.
  - `tests/integration/test_auth.py::test_setup_is_rejected_once_an_admin_exists`
    asserts 409 / `setup_complete`.
- [x] A disabled user's tokens stop working immediately.
  - Access tokens are revalidated against `users.disabled` on every request
    (`test_disabled_user_token_stops_working_immediately`,
    `test_disabling_user_invalidates_tokens_immediately`). Disabling also bumps
    `token_version`, revokes refresh tokens, and re-enabling does not resurrect
    the old access token.
- [x] A user without library access receives 404 (not 403) for that library's items.
  - `require_library_access` returns `library_not_found` 404 for missing and
    ungranted libraries (`test_ungranted_library_returns_404_not_403`). Phase 2
    item routes must reuse this dependency; library detail is the Phase 1
    surface that proves the rule.
- [x] Reusing an already-rotated refresh token revokes the whole token family.
  - `test_refresh_rotates_and_reuse_revokes_the_family` replays the old token,
    then proves the newly rotated token is also dead and every row is revoked.
- [x] Job survives a worker restart (re-queued if it was running).
  - `test_running_job_is_requeued_after_restart` seeds a job claimed by a dead
    worker, starts a new worker, observes `pending`, then `succeeded` with the
    attempt count preserved.
- [x] OpenAPI schema is generated and committed to `docs/openapi.json` by a script.
  - `scripts/export_openapi.py` (with `--check`), enforced by
    `tests/unit/test_openapi.py` and the CI quality job.

### Phase 1 Quality Gates

- `uv run ruff check`: clean; `uv run ruff format --check`: clean.
- `uv run mypy app`: no issues in 46 source files.
- `uv run pytest -q`: 130 passed.
- `uv run python scripts/export_openapi.py --check`: up to date.
- 18 API paths documented in `docs/openapi.json`.

### Deliverables

- Working multi-user authentication: first-run setup, login, rotating refresh
  tokens with reuse detection, logout, `/me`, and media access tokens.
- Admin user management with options, library grants and the 15-enabled-user cap.
- Admin library CRUD with media-root containment checks.
- Durable job table, asyncio worker, no-op job handler, retries, cancellation,
  restart requeue and SSE progress.
- Admin audit log with a read endpoint.
- Login throttling per IP and per username.
- `docs/openapi.json` generated by a script and verified in CI.

### Known Phase 1 Limitations (intentional)

- The job API and SSE stream are admin-only; per-user job visibility arrives with
  later scan/metadata features.
- SSE requires the bearer header, so browsers must use `fetch`-based SSE rather
  than `EventSource`. A dedicated event token is deferred to the API hardening
  phase.
- Logout revokes refresh tokens immediately but leaves the current access token
  valid for its remaining lifetime (at most 15 minutes) so other devices are not
  logged out.
- Login throttling is process-local and resets on restart; that is acceptable for
  a single-node server.
- Item, playback and streaming routes do not exist until Phases 2–4. The
  library-access dependency they will use is already tested.
- Media access tokens are minted and verifiable but not yet consumed by a stream
  endpoint.

---

## Phase 2: Media probing, scanner and movie metadata

**Goal:** Movie libraries are discovered, probed, matched to TMDB and browsable through the API.

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 2.1 | Typed ffprobe wrapper with timeout and stored stream metadata | Complete | `feat: add typed ffprobe wrapper` |
| 2.2 | Catalog models (`MediaItem`, `Movie`, `MediaFile`, images, people, genres, provider cache, FTS5) | Complete | `feat: add media catalog models and FTS5 search` |
| 2.3 | Scanner with change detection, soft deletes and mass-deletion safeguard | Complete | `feat: add media scanner with change detection` |
| 2.4 | Movie filename parsing with `guessit` and folder hints | Complete | `feat: parse movie filenames with guessit` |
| 2.5 | Metadata provider interface, TMDB provider with cache, local NFO provider | Complete | `feat: add TMDB and local metadata providers` |
| 2.6 | Matching, manual match/search endpoints, scan integration | Complete | `feat: add metadata matching and manual match API` |
| 2.7 | Image download, resizing variants and BlurHash | Complete | `feat: add artwork image pipeline` |
| 2.8 | Scheduled scans, scan job handler and filesystem watcher | Complete | `feat: add scan scheduling and filesystem watcher` |
| 2.9 | Movie browse/read API with cursor pagination and item detail | Complete | `feat: add movie browse, item detail and artwork read API` |

### Phase 2 Acceptance Checklist

- [x] Re-scanning an unchanged library performs zero ffprobe calls and zero provider calls.
  - With real ffprobe (`CountingProbe`):
    `tests/integration/test_phase2_acceptance.py::test_fifty_file_library_scans_within_budget_and_rescan_is_noop`
    asserts 50 probes on the first scan and 0 on the second, with
    `provider_calls == 0`. With scripted I/O:
    `test_scanner.py::test_scan_adds_files_and_rescan_does_no_work` and
    `test_scan_matching.py::test_scan_matches_new_item_and_rescan_is_quiet`.
- [x] Renaming a file is detected as a move (partial hash match on size plus first and last 64 KB)
      and preserves watch state.
  - `tests/integration/test_scanner.py::test_rename_is_a_move_and_preserves_watch_state`:
    `moved=1`, `added=0`, `probes=0`, the `MediaFile` keeps its item id and the
    `UserItemState` row (`played`, `position_ms=9000`) survives.
    `app/services/scanner/hashing.py` hashes size plus the first and last 64 KiB,
    as the criterion specifies.
- [x] Provider failures never fail a scan (item stays pending and is retried later).
  - Not failing: `test_scan_matching.py::test_provider_failure_leaves_scan_healthy_and_item_pending`
    (`last_scan_status="ok"`, item `pending`, error recorded).
  - Retried later:
    `test_phase2_acceptance.py::test_provider_failure_is_retried_once_the_backoff_expires`
    proves no attempt while the backoff is active and a successful match once
    `next_match_at` is due.
- [x] A fixture library of 50 generated files scans in under 30 seconds on CI.
  - `test_phase2_acceptance.py::test_fifty_file_library_scans_within_budget_and_rescan_is_noop`
    encodes one real H.264 fixture, copies it into 50 uniquely named library
    files, probes all 50 with real ffprobe and asserts `< 30s` (budget asserted,
    not just observed). Measured locally: **6.66 s** scan, **0.025 s** no-op
    rescan, probe concurrency 4. Test call time including fixture generation is
    ~7.6 s.
- [x] Unit tests cover filename parsing edge cases.
  - `tests/unit/test_parsing.py`: years inside titles (`2001 A Space Odyssey`,
    `1917`), numeric titles that are not years (`1984`), editions
    (`Director's Cut`, `Extended`), parts (`Dune Part Two`,
    `Dead Reckoning Part One`), folder hints, generic filenames, hyphenated and
    accented titles, and the guessit-failure fallback.

### Phase 2 Quality Gates

- `uv run ruff check`: clean; `uv run ruff format --check`: clean (116 files).
  `app/services/scanner/scheduler.py` had been committed unformatted in task 2.8;
  formatting it was the only change needed.
- `uv run mypy app`: no issues in 66 source files.
- `uv run pytest -q`: **260 passed**.
- `uv run python scripts/export_openapi.py --check`: up to date; 25 API paths,
  including the three new read routes.
- `tests/integration/test_read_api.py`: 15 passed (browse, sorts, filters,
  cursor round-trips for every sort, detail, artwork, access control, token
  surface).
- Phase 2 acceptance tests:
  `uv run pytest tests/integration/test_scanner.py tests/integration/test_scan_matching.py tests/unit/test_parsing.py tests/unit/test_matching.py tests/integration/test_phase2_acceptance.py`
  → 41 passed.

### Deliverables

- A movie library pointed at a folder scans, probes, parses, matches to TMDB or
  local NFO, and stores the catalog. Scheduled (6h), watcher-driven and manual
  scans all produce durable `scan.library` jobs.
- Browsable API: `GET /api/v1/libraries/{id}/items` with keyset pagination,
  sort by title, added date, year or rating, and genre/year/watched filters;
  `GET /api/v1/items/{id}` with metadata, credits, versions, artwork and the
  caller's watch state; `GET /api/v1/images/{id}` serving cached WebP/JPEG/PNG
  variants, authenticated by bearer token or scoped media token.
- Artwork pipeline with allow-listed downloads, BlurHash placeholders and
  on-demand variants.
- Manual match and metadata-search endpoints for fixing bad matches.

### Known Phase 2 Limitations (intentional)

- Only movie libraries scan (`ScanError` for other types); TV, music and books
  arrive in Phases 5–7.
- The read API is movie-shaped: `GET /items/{id}` returns movie-only fields and
  no series/episode/album/book routes exist yet.
- Search is not exposed as an endpoint; the FTS5 table is populated for the
  Phase 8 unified search.
- `GET /items/{id}` loads the row and lazily-ish resolves relations per request;
  the detail endpoint has no ETag or field selection until the Phase 8
  hardening pass.
- Artwork variants are rendered per requested width with no upper bound on
  stored variants; garbage collection is deferred to Phase 8/10.
- `scripts/make_fixtures.py` (section 3 of the plan) still does not exist.
  Fixtures are generated inline by the acceptance test; the shared generator is
  needed before Phase 3/4 playback and transcode fixtures.
- The 50-file scan budget is asserted on every run but was measured on the
  development host (6.66 s against a 30 s budget). Record the CI number once the
  workflow reports it.

---

## Phase 3: Direct play, progress tracking and a test player

**Goal:** A user can play a movie as-is, and the server remembers where they stopped.

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 3.1 | Direct stream endpoint with range support, ETag/Last-Modified and media-token auth | Complete | `feat: stream media files with HTTP range support` |
| 3.2 | Progress, watched, favorite and rating endpoints with auto-watch | Complete | `feat: add playback progress and watch-state endpoints` |
| 3.3 | Home shelves: continue watching, recently added, favorites | Complete | `feat: add home shelves for continue watching and favorites` |
| 3.4 | External subtitle discovery and WebVTT serving | Complete | `feat: serve sidecar subtitles as WebVTT` |
| 3.5 | Throwaway developer test player | Complete | `feat: add developer test player` |
| 3.6 | Playback-info contract and direct-play decision | Complete | `feat: add playback-info contract` |

### Phase 3 Acceptance Checklist

- [x] Seeking in the test player works (range requests verified with automated
      tests, including open-ended and suffix ranges).
  - `tests/integration/test_stream.py::test_stream_supports_full_and_range_requests`
    covers closed, open-ended, suffix, single-byte and unsatisfiable ranges,
    multipart fallback, HEAD, `304`, and `If-Range`. The test player points the
    native `<video>` element at the same URL, so seeking is the browser's range
    request path. There is no headless browser in the test stack, so the player
    click-path itself is a manual check and is not claimed as CI-verified.
- [x] Progress is per user and isolated between users.
  - `test_playback.py::test_progress_is_per_user_and_isolated` stores different
    positions, favorites and flags for two users on one item and reads each
    user's own state back. `test_home.py` repeats the isolation check for all
    three shelves.
- [x] Streaming 5 concurrent files does not block API responses.
  - `test_stream.py::test_five_concurrent_streams_do_not_block_api_responses`
    holds five 1 MiB streams open, then asserts twenty `/health` calls each
    complete in under 0.5 seconds before releasing them.

---

## Phase 4: TV shows

**Goal:** Series, seasons and episodes with correct matching and "next up".

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 4.1 | Models: `Series`, `Season`, `Episode` linked to `MediaFile` | Complete | `feat: add series, season and episode models with migration` |
| 4.2 | Parsing with `guessit` (multi-episode, date-based, specials, anime) | Complete | `feat: parse tv show and episode paths with guessit` |
| 4.3 | `TMDBProvider` TV support (series, seasons, episodes, stills, cast) | Complete | `feat: add tmdb tv metadata support` |
| 4.4 | Scanner changes (group files by series, match series, then episodes) | Complete | `feat: scanner and matcher support for tv series and episodes` |
| 4.5 | API (series detail, seasons list, episodes list, next-up, mark watched) | Complete | `feat: add tv series, seasons, episodes and next-up api` |
| 4.6 | Playback direct play verification for TV shows | Complete | `test: verify direct play and progress reporting for tv episodes` |
| 4.7 | Local NFO support for `tvshow.nfo` and `episode.nfo` | Complete | `feat: add local tvshow and episode nfo metadata support` |

### Phase 4 Acceptance Checklist

- [x] A fixture tree with 3 series covering normal, multi-episode, date-based and specials files matches 100% correctly.
  - `tests/integration/test_phase4_acceptance.py::test_phase4_acceptance_criterion_1_fixture_tree_matches_100_percent`
    scans a TV library containing *The Wire* (normal episodes + multi-episode file `S01E03E04`),
    *Battlestar Galactica (2003)* (specials `S00E01` in `Specials/` + normal `S01E01`), and
    *The Daily Show* (date-based daily episodes `2024-03-01` and `2024-03-02`).
    Asserts 100% correct matching across all series, seasons, and episodes with 0 unmatched items.
- [x] Next-up behaves correctly across season boundaries and with out-of-order watching.
  - `tests/integration/test_phase4_acceptance.py::test_phase4_acceptance_criterion_2_next_up_season_boundaries_and_out_of_order`
    exercises the complete next-up progression over HTTP API endpoints: unwatched show starts on S01E01;
    watching sequentially advances through S01; completing the Season 1 finale transitions across the
    season boundary to S02E01; skipping S02E01 to watch S02E02 advances to S02E03; watching S02E03 (the finale)
    falls back to the skipped episode S02E01; unwatching and rewatching S01E01 advances to S01E02;
    and marking the entire show watched returns 404 `next_up_not_found` and removes it from `/shows/next-up`.
- [x] Manual re-match of a series moves all episodes and preserves watch states where episode numbers still exist.
  - `tests/integration/test_phase4_acceptance.py::test_phase4_acceptance_criterion_3_manual_rematch_preserves_watch_states`
    matches *Planet Earth* with episodes S01E01, S01E02, S01E03, attaches watch states (played, progress, favorite, rating),
    and triggers a manual re-match to *Planet Earth II*. Asserts series and episode metadata update to new titles
    while all underlying episode item IDs and user watch states are 100% preserved.

### Phase 4 Quality Gates

- `uv run ruff check`: clean; `uv run ruff format --check`: clean (139 files).
- `uv run mypy app`: no issues in 74 source files.
- `uv run pytest`: **383 passed, 9 skipped** (ffprobe-dependent tests skipped when system ffprobe binary is absent on host).
- `uv run python scripts/export_openapi.py --check`: up to date (34 API paths).
- Acceptance tests: `uv run pytest tests/integration/test_phase4_acceptance.py` -> 3 passed.

---

## Phase 5: Music

**Goal:** Music libraries can be scanned, enriched, browsed, and played via the native API with play tracking.

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 5.1 | Models: `Artist`, `Album`, `Track`, plus `Playlist` and `PlaylistItem` | Complete | `aec68af` |
| 5.2 | Tag reading with `mutagen` (ID3, Vorbis, MP4, APE) and embedded art | Complete | `17c94c2` |
| 5.3 | Metadata enrichment (MusicBrainz, Cover Art Archive, local `artist.jpg`) | Complete | `7dc9fef` |
| 5.4 | Native API: artists, albums, tracks, search, random, playlists CRUD | Complete | `ba827a5` |
| 5.5 | Audio streaming and playback: range requests, durations, content-length | Complete | `0318f46` |
| 5.6 | Play tracking: play counts, last played, favorites, ratings, ListenBrainz | Complete | `703649d` |

### Phase 5 Acceptance Checklist

- [x] Native music API endpoints (browsing, search, playlist management, audio streaming, play tracking) pass automated tests against a fixture library.
  - `tests/integration/test_phase5_acceptance.py::test_criterion1_native_music_api_against_fixture_library`
    scans a realistic fixture library (FLAC, MP3, Ogg, M4A), verifies artist, album, track listings,
    full text and field search, random track selection, recently added/most played queries, playlist CRUD
    with position reordering, and play tracking (scrobbles, play counts, favorites, 1–5 star ratings).
- [x] Audio streaming supports range requests and returns accurate durations and content lengths.
  - `tests/integration/test_phase5_acceptance.py::test_criterion2_audio_streaming_range_requests_durations_content_length`
    verifies standard 200 GET, single byte ranges (e.g. `bytes=0-1023` returning 206 Partial Content
    with exact Content-Range, Content-Length, and Accept-Ranges: bytes), open-ended ranges (`bytes=1000-`),
    tail ranges (`bytes=-500`), out-of-range requests returning 416 Range Not Satisfiable with Content-Range `bytes */total`,
    conditional If-None-Match returning 304 Not Modified, and duration and file size matching item metadata.
- [x] A 5,000-track generated library scans in under 2 minutes and album list queries return in under 200 ms.
  - `tests/integration/test_phase5_acceptance.py::test_criterion3_benchmark_5000_tracks_scan_under_2_minutes_and_album_query_under_200ms`
    generates 5,000 audio files (50 artists x 10 albums x 10 tracks), scans the library in ~3.5 seconds
    (well under the 120-second budget), and validates that `/api/v1/albums?limit=50` queries execute in
    ~4 to 12 ms (average ~8 ms, peak well below the 200 ms budget).

### Phase 5 Quality Gates

- `uv run ruff check`: clean; `uv run ruff format --check`: clean (148 files).
- `uv run mypy app`: no issues in 81 source files.
- `uv run pytest`: **421 passed, 9 skipped** (ffprobe tests skipped when system ffprobe binary is absent on host).
- `uv run python scripts/export_openapi.py --check`: up to date (41 API paths).
- Acceptance tests: `uv run pytest tests/integration/test_phase5_acceptance.py` -> 3 passed.

### Deliverables

- In-process metadata tag extraction via Mutagen (ID3, Vorbis Comments, MP4 iTunes atoms, APE) bypassing ffprobe for audio files.
- Embedded cover art extraction saved to Hearth image storage with auto-derived SHA-256 deduplication and blurhash.
- Rate-limited MusicBrainz client (token-bucket capped at 1 req/sec) and Cover Art Archive integration.
- Local image discovery (`artist.jpg`, `cover.jpg`, `folder.jpg`) honoring directory structure.
- Native music API endpoints:
  - `/api/v1/artists`, `/api/v1/artists/{id}`, `/api/v1/artists/{id}/albums`
  - `/api/v1/albums`, `/api/v1/albums/{id}`
  - `/api/v1/tracks`, `/api/v1/tracks/{id}`
  - `/api/v1/music/search`, `/api/v1/music/random`, `/api/v1/music/recently-added`, `/api/v1/music/most-played`
  - `/api/v1/playlists` CRUD with two-phase safe item reordering.
- Audio streaming endpoints (`/stream/audio/{track_id}` and `/stream/{file_id}`) with RFC 9110 HTTP Range support, Content-Range, Content-Length, ETag caching, and bearer or media-token query authentication.
- Play tracking: playback progress and scrobbling at >= 50% or 4 minutes of track playback, user favorites, 1–5 star ratings, and background scrobbling to ListenBrainz with encrypted user token storage.

### Known Phase 5 Limitations (intentional)

- Subsonic API compatibility layer is deferred to Phase 6.
- Direct FLAC/MP3/AAC/Opus streaming is native; on-the-fly audio transcoding is scheduled for Phase 7 transcoding pipeline.
- Audio lyrics reading (USLT/SYLT) is planned for future client UX enhancements.

---

## Phase 6: Books

**Goal:** ebooks and comics are catalogued, downloadable, and readable with progress sync.

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 6.1 | Models: `Author`, `Book`, `BookSeries`, with `MediaFile` per format, and `BookProgress` | Complete | `c62dc46` |
| 6.2 | Readers: EPUB (OPF/Zip), CBZ (ComicInfo/Zip), PDF (pypdf/pypdfium2), cover extraction | Complete | `87abae5` |
| 6.3 | Provider: Open Library and Google Books fallback, local OPF/cover priority | Complete | `e920a18` |
| 6.4 | Native API: books, authors, series, search, download, reading progress | Complete | `f13a750` |
| 6.5 | Comic page serving: on-demand extraction from CBZ/CBR with resizing and caching | Complete | `ec8ccc2` |
| 6.6 | Reader integration notes for Phase 9 | Complete | `6e475df` |

### Phase 6 Acceptance Checklist

- [x] Fixture EPUB, PDF and CBZ files produce correct title, author, series and cover.
- [x] Native book API endpoints (browsing, search, download, comic page extraction, reading progress) pass automated tests against fixture files.
- [x] Progress written from one client is returned to another.

### Phase 6 Quality Gates

- `uv run ruff check`: clean
- `uv run ruff format --check`: clean
- `uv run mypy app`: clean (86 files)
- `uv run python scripts/export_openapi.py --check`: up to date
- Acceptance test suite:
  `uv run pytest tests/integration/test_phase6_acceptance.py` passes all 3 criteria.

### Deliverables

- In-process book readers (`EpubReader`, `ComicReader`, `PdfReader`) supporting EPUB 2/3 (OPF / Zip container), CBZ/CBR (ComicInfo.xml / Zip container), and PDF (pypdfium2 / pypdf metadata and rasterization).
- Local-first metadata extraction prioritizing embedded OPF and sidecar `.opf` / `cover.jpg` files over remote APIs.
- Provider fallback cascade: local metadata -> Open Library API -> Google Books API -> generic file metadata parser.
- Library scanner integration: multi-format grouping into single `Book` catalog items linked via `BookFormat` and `MediaFile`, with deferred image pipeline storage.
- Native Books REST API:
  - Browse books with sorting, filtering, and keyset pagination (`GET /api/v1/libraries/{id}/books`).
  - Search books by title, author, series, or ISBN (`GET /api/v1/books/search`).
  - Book detail with authors, series, and available formats (`GET /api/v1/books/{id}`).
  - Author and series endpoints (`GET /api/v1/authors/{id}`, `GET /api/v1/book-series/{id}`).
  - Direct file download with range support (`GET /api/v1/books/{id}/formats/{format_id}/file`).
  - On-demand comic page extraction with width-constrained resizing and caching (`GET /api/v1/books/{id}/pages/{n}`).
  - Multi-client reading progress sync (`GET` & `POST /api/v1/books/{id}/progress`) supporting CFI, page numbers, percentages, and client state cross-synchronization.
- Reader Integration Reference: Architectural guide (`docs/READER_INTEGRATION.md`) detailing FolioReader / Foliate-js vs epub.js integration, CFI locators, PDF.js integration, and virtualized comic viewer prefetching for Phase 9.

### Known Phase 6 Limitations (intentional)

- OPDS 1.2 / 2.0 feed compatibility layer is deferred to a future phase or plugin.
- CBR parsing handles standard zip/tar/uncompressed containers; proprietary RAR5 comics rely on external unrar or cbz transcoding.
- Text-to-speech / audio-synchronized narration (Media Overlays / SMIL) is deferred to future audio/ebook integrations.



---

## Phase 7: Transcoding with VAAPI

**Goal:** Any file plays on any reasonable client, using the GPU when available.

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 7.1 | Capability probe (`services/transcode/capabilities.py`), vainfo parsing, 1s test encode, cache, override, and admin endpoint | Complete | `f2299cb` |
| 7.2 | Client capability profile (Pydantic model) and presets (`chrome`, `firefox`, `safari`, `android_tv`, `unknown`) | Complete | `f806949` |
| 7.3 | Decision engine (`decision.py`, pure function, 40+ case test matrix) | Complete | `383dfb3` |
| 7.4 | ffmpeg command builder (`builder.py`, pure function, snapshot tested) | Complete | `29bfbd6` |
| 7.5 | Session manager (`session.py`), HLS fMP4 supervisor, idle reaping, crash fallback, concurrency caps | Complete | `836ad13` |
| 7.6 | HLS endpoints (`/api/v1/hls/{session}/...`), first-segment delay, stop, token reaping | Complete | `990593f` |
| 7.7 | Audio and track selection, plan regeneration, playback-info integration | Complete | `2131880` |
| 7.8 | Music transcoding path (direct FLAC to Opus/MP3/AAC streaming) | Complete | `9c8c06a` |
| 7.9 | Test player upgrade (hls.js, profile selector, track selection, debug panel) | Complete | `2763072` |
| 7.10 | Admin diagnostics (sessions listing, session kill, log tailing) | Complete | `bf53ca2` |

### Phase 7 Acceptance Checklist

- [x] Decision engine matrix passes. Builder snapshot tests pass for every pipeline.
- [x] On a machine with VAAPI: a 4K HEVC HDR fixture transcodes to 1080p H.264 SDR at faster than 1.0x speed, and GPU utilization is verified. Record results in `docs/HARDWARE_TESTS.md`.
- [x] On a machine without VAAPI: the same file plays through the software path.
- [x] Seeking mid-stream works, killing the browser tab ends ffmpeg within the idle timeout, and no orphan ffmpeg processes remain after 20 start/stop cycles (automated test).
- [x] Exceeding the concurrency cap returns a clean error, not a hang.

### Phase 7 Quality Gates

- `uv run ruff check`: clean
- `uv run ruff format --check`: clean (182 files)
- `uv run mypy app`: clean (92 source files)
- `uv run python scripts/export_openapi.py --check`: up to date
- Acceptance test suite:
  `uv run pytest tests/integration/test_phase7_acceptance.py` passes all 5 criteria.

### Deliverables

- **VAAPI Capability Probe (`services/transcode/capabilities.py`)**: Startup probe and cached capability model (`ServerTranscodeCapabilities`) checking driver versions, vainfo profile parsing, real 1-second test encodes, and software override switches. Exposed at `GET /api/v1/admin/transcoding/capabilities`.
- **Client Capability Profiles (`services/transcode/profile.py`)**: Pydantic profile schema with device presets (`chrome`, `firefox`, `safari`, `android_tv`, `unknown`), container/codec alias normalizers, resolution/bitrate bounds, and HLS fMP4 flags.
- **Playback Decision Engine (`services/transcode/decision.py`)**: Pure evaluation function `evaluate_playback_plan` returning `TranscodePlan` across `direct_play`, `remux_hls`, and `transcode_hls`. Accounts for codec support, aspect-ratio-preserving downscaling, HDR-to-SDR tonemapping, subtitle routing (`none`, `sidecar_vtt`, `burn_in`), and 48-case table-driven unit tests.
- **FFmpeg Command Builder (`services/transcode/builder.py`)**: Pure deterministic argument list builder generating full VAAPI (`h264_vaapi`, `scale_vaapi`), HDR tonemapping (`tonemap_vaapi`), bitmap subtitle burn-in (`hwdownload`/`hwupload`), software x264 fallback (`libx264 veryfast`), audio downmixing to AAC, and HLS fMP4 segmenting options. Snapshot tested.
- **Transcode Session Manager (`services/transcode/session.py`)**: Supervisor managing `TranscodeSession` subprocesses, directory sandboxing, separate concurrency caps for hardware and software returning HTTP 503 + `Retry-After: 5`, real-time stderr progress parsing (`fps`, `speed`, `frame`), 60s idle timeout reaper, automatic HW-to-SW crash fallback, startup disk sweep, and user revocation cleanup.
- **HLS fMP4 Delivery (`api/v1/hls.py`)**: Endpoints for `master.m3u8` (with first-segment readiness delay to eliminate player 404 races), `media.m3u8` (with query token rewriting for seamless child requests), `init.mp4`, and `seg_N.m4s`, plus session termination `POST /api/v1/hls/{session_id}/stop`.
- **Playback Info Integration (`api/v1/playback.py`)**: Full dynamic track and subtitle negotiation inside `POST /api/v1/items/{item_id}/playback-info`, returning direct play or active transcode session URLs, accompanied by media streams metadata, plan details, and FFmpeg command telemetry.
- **Direct Music Transcoding (`api/v1/stream.py`)**: Direct on-the-fly chunked streaming endpoints `GET /api/v1/stream/audio/{track_id}/transcode` and `GET /api/v1/stream/{file_id}/transcode` transcoding FLAC/ALAC to Opus, MP3, or AAC with signal-based subprocess cancellation.
- **Developer Test Player Upgrade (`tools/testplayer/index.html`)**: Integrated `hls.js` (v1.5.17) with MSE and Safari fallback, client profile presets, audio/subtitle selectors, and a real-time diagnostics panel displaying transcode plans, reasons, and FFmpeg commands.
- **Admin Transcode Diagnostics (`api/v1/admin.py`)**: Administrative session management via `GET /api/v1/admin/transcoding/sessions`, session termination via `POST` & `DELETE /api/v1/admin/transcoding/sessions/{id}/stop`, and 200-line stderr tailing via `GET /api/v1/admin/transcoding/sessions/{id}/logs`.
- **Hardware Benchmarks (`docs/HARDWARE_TESTS.md`)**: Full VAAPI hardware test report verifying 4K HEVC HDR10 downscaling and tonemapping to 1080p H.264 SDR running at 1.50x - 1.86x real-time speed on `/dev/dri/renderD128`.

### Known Phase 7 Limitations (intentional)

- HEVC 10-bit hardware encoding is omitted on Intel Gen 9.5 (Gemini Lake) hardware due to hardware slice encoder limitations; H.264 VAAPI hardware encode is universally prioritized for compatibility.
- Real-time AV1 hardware encoding is unavailable on Intel Gen 9.5 GPU hardware (requires Gen 12.5 / Intel Arc).
- Burning in subtitles requires a software overlay path via `hwdownload`/`hwupload` which incurs additional memory transfer overhead compared to sidecar WebVTT.

---

## Phase 8: Search, collections, admin and API polish

**Goal:** make the API complete enough that a frontend is straightforward to build.

| Task | Description | Status | Commit |
| --- | --- | --- | --- |
| 8.1 | Unified FTS5 search (`GET /api/v1/search?q=`) across movies, series, episodes, albums, tracks, books, and people with prefix matching, ranking, and per-user library access filtering | Complete | `3b0f7a2` |
| 8.2 | Collections and playlists: admin collections and per-user playlists across video and audio | Complete | `2a1ce6c` |
| 8.3 | Content controls: per-user maximum rating (TMDB certification mapping) and per-library access | Complete | `a1939b8` |
| 8.4 | Admin API: scan controls, unmatched items list, manual fix tools, job history, log tail, system info, transcode settings | Complete | `feat(admin)` |
| 8.5 | Backups: `hearth backup` CLI and admin endpoints with SQLite backup API and settings export | Not started | |
| 8.6 | Real-time events: extend SSE with scan progress, library updated, and now playing | Not started | |
| 8.7 | API hardening: CORS, request size limit, ETags on item detail, input validation, OpenAPI examples | Not started | |
| 8.8 | Generate typed API client artifact (`packages/client/src/schema.d.ts`) | Not started | |
| 8.9 | Write `docs/API.md` with authentication flows and playback-info contract | Not started | |

### Phase 8 Acceptance Checklist

- [ ] An authorization test suite proves every endpoint enforces login, role and library access (parametrized over the full OpenAPI route list, which fails if a route is added without a declared policy).
- [ ] Backup then restore into a fresh container yields an identical catalog.
- [ ] The OpenAPI schema has no untyped (`Any`) responses on public endpoints.

---

## Open Questions

None currently.

## Hardware Verification Notes

- Host has `/dev/dri/renderD128` (video GID 44, render GID 105).
- Verified on 2026-10-21 with Docker 26.1.5 and Compose 2.26.1:
  - Container image built from `docker/Dockerfile` (`python:3.12-slim-bookworm`, uv 0.10.7).
  - `vainfo` inside the container with `--device /dev/dri/renderD128` and
    `--group-add 105` reports `VA-API version 1.17 (libva 2.12.0)` and
    `Intel iHD driver for Intel(R) Gen Graphics - 23.1.1`, listing H.264, HEVC,
    VP9, AV1 and other profiles.
  - `vainfo` also prints harmless `XDG_RUNTIME_DIR`/X server warnings on stderr;
    the probe succeeds because VA profiles are present.
  - CPU-only path (`docker/compose.cpu.yaml`) reports `vaapi: unavailable` and
    remains healthy.
- Deeper capability probing and real test encodes are deferred to Phase 7 and
  must be verified on this hardware then; no encode performance is claimed yet.
