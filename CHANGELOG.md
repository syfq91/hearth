# Changelog

All notable changes to Hearth are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- The admin **Unmatched** tab now shows the full path of each item's source file(s) (rolling up episode files for series/seasons), accepts an editable metadata search query, and lets the administrator choose which online provider to search via `GET /api/v1/metadata/providers`. `UnmatchedItemResponse` includes a `file_paths` field.
- A video quality selector (`Auto`, `SD`, `HD`, `FHD`, `4K`) in the player controls. The choice is sent as the new `quality` field on `POST /items/{id}/playback-info` and maps to server-side resolution/bitrate ceilings intersected with device and user limits (ADR 071). Changing it renegotiates playback and resumes at the same position with a fresh transcode session.
- Structured `playback_decision` logging on every playback-info request: capability source and preset, reason union, chosen encoder targets and dimensions, hardware gate, and the resulting stream URL or session id in one record. The decision engine remains a pure function.
- Conditional `transcode_targets` in the playback-info capability profile: an ordered list of container/codec targets with typed resolution, bitrate, level and audio-channel ceilings. Targets are filtered by server capability and fMP4/HLS compatibility, ranked by source-codec match, and their ceilings shape the transcode output. A `TranscodeTargetNotSupported` reason reports when no requested target could be honoured (ADR 069).
- Ordered `video_transcode_codecs` and `audio_transcode_codecs` in the playback-info capability profile. Clients can now state which encoder targets they prefer (for example HEVC before H.264); the server filters that list through its own verified encoders and an HLS codec allow-list and prefers a target matching the source codec. Defaults remain H.264/AAC, so existing clients are unaffected.
- `embedded_subtitle_urls` on playback-info: a client-renderable URL and format for every embedded text subtitle stream, keyed by the container stream index. The web player now switches embedded text tracks mid-playback without tearing down and re-creating the HLS session; only bitmap tracks (which require burn-in) still renegotiate.
- `shift_ms` query parameter on the WebVTT subtitle endpoints (`/api/v1/subtitles/{id}.vtt` and `/api/v1/media/{file_id}/subtitles/{index}.vtt`) that rewrites cue boundaries, so the user's subtitle timing calibration also applies to native `<track>` playback instead of only to JASSUB. The web player re-fetches the track (debounced) when the offset changes.
- Container font attachment extraction and serving (`GET /api/v1/media/{file_id}/attachments/{idx}`), exposing embedded fonts in MKV files with disk caching for client-side ASS/SSA WebAssembly rendering (Phase 1 of video player overhaul).
- Embedded subtitle delivery (`GET /api/v1/media/{file_id}/subtitles/{index}.vtt` and `.ass`) plus `embedded_subtitle_url`/`embedded_subtitle_format` on playback-info, so direct-play clients and client-side ASS renderers can show text subtitles without a transcode session.
- Subtitle burn-in fallback parameter (`force_burn_in_subtitles`) on `POST /api/v1/items/{id}/playback-info` allowing clients to cleanly recover from WebAssembly crashes without stream teardown.
- Comprehensive video transcoding pipeline overhaul:
  - **Atomic segment writes (T01)**: Implemented `-hls_flags temp_file` with atomic renaming and `.tmp` route rejection to eliminate partial segment reads.
  - **Forced keyframes at segment boundaries (T02)**: Added `-force_key_frames expr:gte(t,n_forced*seg_s)` and `-sc_threshold 0` for libx264, and `-g`/`-keyint_min` for VA-API to guarantee exact IDR boundaries.
  - **Non-destructive seeking & cache reuse (T03)**: Preserved on-disk segments across seeks (INV-8), aligned first post-seek PTS via `-output_ts_offset`, and added 503 `Retry-After` bounded waits for pending segments.
  - **Client capability detection (T04)**: Added MSE-based capability probe (`detectBrowserCapabilities`) in the web client with browser/device preset fallbacks (Chrome, Safari, Firefox, Android TV) and 422 validation.
  - **Decision engine audit (T05)**: Full reason union computation, exact remux rule enforcement, and safe 40 Mbps defaults for unknown bitrates.
  - **Transcode throttling (T06)**: Supervisor loop pausing ffmpeg processes via `SIGSTOP` when lookahead lead $\ge 60\text{s}$, resuming via `SIGCONT` at $\le 20\text{s}$ or upon client catchup, with INV-6 termination safety and structured logging.
  - **Circuit breaker & 503 semantics (T07)**: Tracked transcode startup/exit failures with cooldown periods and emitted 503 `Retry-After` with `transcode_circuit_open`.
  - **Dynamic RFC 6381 codec strings (T08)**: Dynamically generated standard codec strings in HLS master playlists for H.264, HEVC, AV1, AAC, and AC-3.
  - **Active request accounting & idle reaper (T09)**: Tracked active HTTP transfers with `TrackedFileResponse`, ensuring active downloads are never prematurely reaped.
  - **Subtitle pipeline (T10)**: Exposed text subtitles (SRT/ASS/WebVTT) as HLS `SUBTITLES` groups without video transcoding, supported external ASS delivery when requested by client profile, and implemented safe symlink-based burn-in for bitmap formats (PGS/DVD).
  - **Frontend resilience & beacon stop (T11)**: Configured 503 `Retry-After` backoff in Video.js VHS and `hls.js` loaders, updated progress reporting to 10s intervals with immediate pause reporting, and added `navigator.sendBeacon` stop requests on `pagehide` and unmount with query/body token support.
  - **Path & token safety (T12)**: Enforced strict segment name regex (`^(init\.mp4|seg_\d{1,6}\.(m4s|ts))$`), session ID validation, session containment assertions, and HMAC-signed media access tokens.
- Admin **Logs** tab and enhanced `GET /api/v1/admin/logs` endpoint supporting category filtering (`scanning`, `transcoding`, `activity`, `error`, `jobs`, `metadata`), text search, live auto-refresh tailing, log entry expansion, and JSON export.
- BlurHash placeholder rendering in the web client using `fast-blurhash` with smooth image crossfades and memory caching.
- Composite `GET /api/v1/home` endpoint returning continue watching, continue reading, next up, recent libraries, and recent albums in a single round-trip.
- Configurable SQLite connection pool parameters (`HEARTH_DB_POOL_SIZE` and `HEARTH_DB_MAX_OVERFLOW`).
- Composite database indexes for continue-watching, continue-reading, and favorites.
- Dedicated [`docs/development/performance-audit.md`](docs/development/performance-audit.md) performance review and resolutions log.
- Admin **Metadata** tab and `GET`/`PUT /api/v1/admin/metadata/settings` for managing online metadata provider API keys. TMDB and Google Books keys are stored in the database, override environment values, and are picked up by the worker without a restart. `HEARTH_GOOGLE_BOOKS_API_KEY` adds Google Books API-key support.

### Changed
- The inert hls.js rendition `<select>` was replaced by the new quality menu; Hearth still emits a single HLS rendition, so quality switches negotiate a new transcode rather than switching ABR levels mid-stream (ADR 071).
- Container base image moved from Debian 12 (bookworm) to Debian 13 (trixie), upgrading the bundled FFmpeg from 5.1 to 7.1.5 and the Intel VA-API driver to 25.2.x (ADR 070).
- The web client now advertises ordered encoder preferences and one conditional fMP4 transcode target alongside its pass-through codecs, and refreshes cached capabilities when the display changes or the window regains focus. The developer test player gained a "Prefer HEVC target" switch to exercise conditional negotiation.
- Audio bitrate for HLS transcodes is now derived from the effective total bitrate cap when one is set, with the admin-configured bitrate acting as a ceiling rather than a fixed value. Tight caps (≤ 500 kbps) now get 128 kbps audio instead of 192 kbps.
- Page loaders for book reading and watch now fetch independent detail/progress/next-up data in parallel; library text filters are debounced, offscreen shelf cards use `content-visibility`, first cards load images eagerly with high fetch priority, and audio prewarming uses `preload="metadata"` with debounced localStorage writes.
- The API client now caches GET responses for 30 seconds, deduplicates concurrent identical GETs, sends `If-None-Match` and reuses cached bodies on 304, and drops the cache when the access token changes. `cache: false` opts out per request.
- The PDF reader now streams the document by URL with range requests and renders only a small window around the current page, with previous/next controls and page cleanup, instead of downloading and rendering the whole book.
- The book reader route now loads EPUB, PDF, and comic viewers with format-gated dynamic imports instead of statically importing all three on every reader visit.
- Open Library and Google Books provider calls now use the shared `provider_cache` table and a bounded rate limiter, with one retry on HTTP 429; API keys are excluded from cache keys. The backward-compatible adapters now receive the app session factory so caching works for matcher-created providers too.
- The admin metadata API now reuses one matcher and its provider HTTP clients for the whole app instead of constructing them per request; changing metadata credentials invalidates the shared matcher so new settings take effect immediately.
- Metadata matching now uses bounded concurrency for items, top-3 candidate detail lookups, and TV season fetches. TMDB and MusicBrainz rate limiting reserves a slot under the lock but sleeps outside it, so concurrent callers no longer serialize the whole provider.
- Scanner tag, OPF, and filename parsing now runs in bounded worker threads before the write transaction; cancellation is polled at most once per second instead of opening a DB session per file. The final DB write still commits atomically, but no longer holds the writer lock during file parsing.
- `/api/v1/home` now runs its shelves concurrently on separate short-lived sessions instead of awaiting each one in turn; per-library shelves are batched the same way. `/api/v1/shows/next-up` now filters and limits series in SQL and batches series, episode, watch-state, and season-title lookups instead of issuing four queries per series.
- Added SQLite indexes for job/audit ordering and library-scoped media sorting (`jobs.updated_at`, `audit_log.created_at`, `(library_id, year)`, `(library_id, rating)`) plus per-connection `cache_size`, `temp_store`, and WAL pragmas, via Alembic revision `0014_query_indexes`.
- Collection auto-collect now selects only movie IDs and `provider_ids` instead of loading every `MediaItem` with its relationships, so creating a TMDB collection no longer scales with the full movie library.
- Argon2id password hashing and verification now run in worker threads during setup, login, user creation, and password reset, so a 30–80 ms hash no longer blocks the event loop and every concurrent stream. The cached timing-equalization placeholder is also created off-loop on first unknown-user login.
- `/api/v1/health` now caches ffmpeg and VAAPI probe results for 30 seconds (5 seconds for failures) with single-flight deduplication, so container healthchecks no longer spawn a subprocess per request. The read-only database check still runs on every call.
- Video player control bar layout: playback speed, subtitle timing and stream info each have their own control-bar button again; elapsed time sits left of the seek bar with a remaining-time countdown on the right instead of the total duration; volume sits in the left cluster with its slider hidden until the speaker icon is hovered or focused; and the projected finish time ("End at 10:32 PM") is centered in the controls row.
- The stream-info popup gained a fourth line with the delivered bitrate and the measured network throughput.
- Reorganized documentation into `docs/guides/`, `docs/development/`, `docs/reference/`, `docs/decisions/`, and `docs/design/`, moved completed plans and release history into `docs/archive/`, added a `docs/README.md` index, and fixed cross-links and local file references that pointed at the old flat layout.
- Video player controls: the stream-info toggle moved into the control bar, surface taps toggle the overlay instead of pausing, audio/subtitle/speed selectors became icon menus, the aspect-ratio label was removed, controls auto-hide after 1.5s, and the info popup now shows three lines (playback mode, video path, audio path) in the top-right corner.
- Hardware-accelerated transcode processes are no longer started with `nice(10)`. Lowering priority starved the first segment whenever the host CPU was busy (measured 14s vs 2.2s to first segment on a 2-core Celeron while the API was active); software encodes still yield to the API and worker.
- Video transcodes now open with a shorter 2s HLS segment (`HEARTH_TRANSCODE_FIRST_SEGMENT_DURATION_SECONDS`) instead of waiting for a full 4s segment before the master playlist responds. Keyframes stay anchored to the 2s-offset grid, so later segments keep their configured duration and seeking/subtitles stay aligned. Measured on a Celeron J4005 with VA-API, the first segment arrives in 1.6-2.3s instead of 4.2-4.9s.
- Converted HTTP middlewares (`RequestSizeLimitMiddleware`, `RequestIDMiddleware`, `SecurityHeadersMiddleware`) to pure ASGI middleware to remove context-switch and streaming wrapper overhead.
- Optimized `GET /items/{id}` ETags to derive from item and watch state timestamps, short-circuiting 304 Not Modified responses before model detail generation.
- Optimized `GET /home/recently-added` to evaluate deduplicated lightweight scalar tuples before loading full ORM models and selectin relationships.
- Reused application-state `ImagePipeline` singleton across image requests and moved blurhash computation to a worker thread to eliminate IPC overhead.
- Scoped book folder discovery queries to the active directory prefix.
- Updated web client home page to fetch all shelves in a single request, eliminating client-side API waterfalls.
- Dynamically split `JASSUB` in the video player route, loading WebAssembly assets only when ASS/SSA subtitles are rendered.
- Embedded subtitle text tracks are now extracted once into a revision-keyed disk cache, with every text track produced in a single FFmpeg demux pass. Opening a second track or replaying a title no longer re-reads the whole video, which previously delayed the first cue by seconds on large files; the extraction deadline also rose from 30s to 5 minutes so slow storage no longer silently yields an empty track. The web player fetches the JASSUB module in parallel with the subtitle document.
- Online metadata providers are now configured exclusively through **Admin → Plugins**. Both the API and the worker resolve providers from persisted plugin configuration, so changes apply without a restart and there is no secondary environment-variable source of truth.

### Removed
- `GET`/`PUT /api/v1/admin/metadata/settings`, the `metadata.*` credential override layer, and the backward-compatible metadata provider adapter modules in `app/services/metadata/`. `HEARTH_TMDB_API_KEY`, `HEARTH_GOOGLE_BOOKS_API_KEY`, `HEARTH_MUSICBRAINZ_ENABLED`, `HEARTH_OPENLIBRARY_ENABLED`, and `HEARTH_GOOGLE_BOOKS_ENABLED` no longer affect provider configuration; re-enter keys under **Admin → Plugins**.
- Legacy playback-info query aliases (for example `?VideoBitrate=`, `?maxbitrate=`, `?forceBurnInSubtitles=`) and the one-time attachment re-probe: playback-info now uses only its validated request body and the probe data stored by the scanner.
- PascalCase client-capability keys (`Format`, `Method`, `SubtitleProfiles`), the `tv` media-type alias, and the unused Video.js VHS retry interceptor.
- The `hls_fmp4` client capability flag and the `default_max_bitrate` field on the admin transcoding settings API. Neither ever affected playback: Hearth only emits fMP4 HLS, and no `Settings.default_max_bitrate` existed to read.

### Fixed
- Video playback no longer blocks on an unnecessary `next-up` request for movies. The watch route only fetches a series' next-up hint after the item is known to be a series, so a slow or stalled request can no longer prevent the player from mounting.
- Finished items no longer open the player and jump straight to the end. The near-end resume guard now checks the server resume point (it previously checked a value that was always positive by that point, making the guard unreachable).
- The API client no longer emits an unhandled rejection for failed cacheable GETs, and expired cache entries now send `If-None-Match` so a 304 can reuse the cached body as intended.
- The developer test player's "Max Resolution" selector now sends `max_width` and `max_height` instead of the ignored `max_resolution` key, so the resolution cap actually reaches the playback decision.
- Subtitles no longer freeze on the last cue when JASSUB's `requestVideoFrameCallback` loop stalls (background tab, PiP transition, hls.js media swap). The player now reconciles the rendered cue on every `timeupdate`, not only while paused.
- Switching subtitle tracks no longer races. Two overlapping loads could both pass the active-track check (it was assigned after `await clearTrack()`) and append two `<track>` elements; the superseded element was never removed because only the newest one was tracked, producing doubled or garbled cues. Mounts are now serialized and tagged with a generation that is re-checked after every await.
- Enabling a subtitle track mid-playback no longer shifts it by the resume position. The client treated the playback resume point as a subtitle time offset on top of JASSUB, but the HLS pipeline already rebases segment timestamps with `-output_ts_offset`, so `video.currentTime` is on the media timeline; ASS tracks toggled at 20:00 were rendered 20 minutes late.
- Concurrent progress and favorite writes no longer fail with `RuntimeError: ... is bound to a different event loop` after the ASGI lifespan restarts (uvicorn reload, or a new test session). The process-wide playback state lock was a plain `asyncio.Lock`, which binds to the first loop that contends on it; it now follows the running loop.
- The hardware capability probe now runs its encoder smoke tests concurrently and is bounded by `HEARTH_TRANSCODE_CAPABILITY_PROBE_TIMEOUT_SECONDS` (default 20s), so a wedged driver can no longer delay API startup past the container health-check window.
- `HEARTH_TRANSCODE_VAINFO_TIMEOUT_SECONDS` (default 15s) replaces the hardcoded 5s VA-API probe deadline, and the encoder smoke-test deadline now defaults to 15s. Low-power iGPUs needed 2.5-4.5s for the first driver initialisation, so both deadlines previously expired on ordinary hardware.
- A slow hardware capability probe no longer demotes the process to software encoding until restart. On low-power iGPUs (for example a Celeron J4005) `vainfo` can take close to the probe deadline; the timeout was cached as "no hardware encoder", after which every session used `libx264` and the first HLS segment took ~27s instead of ~4s. Timed-out probes are now flagged as inconclusive (`probe_incomplete` on the admin capability response) and re-run in the background up to `HEARTH_TRANSCODE_CAPABILITY_PROBE_RETRIES` times.
- Client-side ASS/SSA rendering now loads container fonts during direct play. The player rebuilt attachment URLs from an HLS-only media token, so font requests were unauthenticated and JASSUB silently fell back to its bundled font; it now uses the token-scoped `attachments[].url` returned by playback-info.
- Server-side text subtitle burn-in now renders with the container's embedded fonts. FFmpeg's `subtitles` filter only sees fontconfig by default, so a styled ASS track fell back to a system font; the transcode session now extracts the file's font attachments into a session `fontsdir` and passes it to the filter.
- Libraries scanned before attachment metadata was persisted no longer lose embedded fonts. Playback-info detects the older probe shape, re-probes the file header once, and stores the repaired `attachments` list.
- MKV files are no longer treated as WebM for direct-play negotiation. ffprobe reports both containers as `matroska,webm`, so clients that only claim WebM support were handed `video/x-matroska` bytes; the playback decision now disambiguates them using the real file extension and remuxes MKV instead.

### Removed
- Removed AMD hardware transcoding support and `mesa-va-drivers` from the container image; VA-API hardware acceleration is dedicated to Intel QuickSync, with automatic fallback to CPU software transcoding (`libx264`).
- Removed AMD compose device (`/dev/kfd`) and `VIDEO_GID` configurations from `docker/compose.yaml`.
- Removed duplicate legacy `lucide-svelte` dependency from the web client in favor of `@lucide/svelte`.

## [1.0.0] - 2026-11-03

First production release of Hearth, a self-hosted personal media server for movies, TV shows, music, and books.

### Added

#### Media Catalog & Library Scanner
- Multi-library support across Movies, TV Series, Music, and Books/Comics.
- Filesystem scanner with SHA-256 partial file hashing to detect modifications and moved files.
- Local-first metadata scraping prioritizing `.nfo` files and sidecar images (`poster.jpg`, `cover.jpg`) with automatic fallback to TMDB (The Movie Database), Open Library, and Google Books.
- Automatic periodic rescans and inotify-driven change detection.

#### Hardware-Accelerated Transcoding (VA-API)
- Automatic GPU hardware capability probing for Intel QuickSync (`iHD` / `i965`) and AMD Radeon (`radeonsi`).
- Dynamic playback decision engine evaluating client profiles (`chrome`, `firefox`, `safari`, `android_tv`) against media codecs, resolution, bit depth, and HDR color spaces.
- Real-time HLS fMP4 segmentation with hardware-accelerated downscaling and tonemapping (HDR10 to SDR BT.709).
- Subtitle routing with automatic WebVTT sidecar extraction and fallback bitmap burn-in.
- Multi-tier transcode concurrency limits and automated idle session termination.
- Seamless crash recovery falling back from GPU to CPU software transcoding (`libx264 veryfast`).

#### Music & Audio Streaming
- Direct byte-range audio streaming for FLAC, MP3, AAC, and Opus.
- On-the-fly music transcoding to Opus/MP3/AAC for bandwidth-constrained devices.
- Artist and album catalog indexing with disc and track groupings.
- ListenBrainz integration with encrypted user token storage and asynchronous scrobble submission.

#### Books & Comics
- Multi-format book reader supporting EPUB (OPF parsing), PDF (vector and canvas rendering), and CBZ/CBR comic archives.
- On-demand server-side comic page extraction, resizing, and caching.
- Cross-client reading progress synchronization (EPUB CFI locators, PDF page numbers, comic indices).

#### Frontend & 10-Foot TV Experience
- Modern, lightweight responsive web application built with SvelteKit and Svelte 5 runes.
- Dedicated 10-foot TV mode with spatial focus navigation for living room D-pad and remote controls.
- Accessible media players with keyboard shortcuts, focus indicators, captions, and reduced-motion preferences.
- Persistent global audio player with playlist queue and Media Session API integration for system media controls.
- Full-text search (FTS5) across movies, series, episodes, music artists, albums, tracks, and books.

#### Hardening, Resilience & Observability
- Fault-tolerant database operations with SQLite Write-Ahead Logging (WAL) and concurrency serialization.
- Server restart recovery: orphaned transcode temporary files cleaned automatically on startup, in-flight jobs requeued, and watch progress preserved.
- Full disk protection rejecting new transcode sessions cleanly with HTTP 503 (`Retry-After`) when cache space is low.
- Zero-downtime atomic backups via SQLite Online Backup API (`hearth backup` CLI and admin endpoints).
- Optional Prometheus `/metrics` exposition (uptime, process memory, transcode stream concurrency, DB pool size, HTTP request rates).
- Administrative log viewer with level filtering and search across in-memory ring buffers.
- Default-deny CORS, strict security headers, path traversal protection, media token scope isolation, and secret redacting.
- Release engineering with multi-architecture container images (`linux/amd64`, `linux/arm64`).
