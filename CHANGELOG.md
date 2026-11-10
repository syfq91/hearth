# Changelog

All notable changes to Hearth are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added
- Admin **Logs** tab and enhanced `GET /api/v1/admin/logs` endpoint supporting category filtering (`scanning`, `transcoding`, `activity`, `error`, `jobs`, `metadata`), text search, live auto-refresh tailing, log entry expansion, and JSON export.
- BlurHash placeholder rendering in the web client using `fast-blurhash` with smooth image crossfades and memory caching.
- Composite `GET /api/v1/home` endpoint returning continue watching, continue reading, next up, recent libraries, and recent albums in a single round-trip.
- Configurable SQLite connection pool parameters (`HEARTH_DB_POOL_SIZE` and `HEARTH_DB_MAX_OVERFLOW`).
- Composite database indexes for continue-watching, continue-reading, and favorites.
- Dedicated [`docs/PERFORMANCE_AUDIT.md`](docs/PERFORMANCE_AUDIT.md) performance review and resolutions log.
- Admin **Metadata** tab and `GET`/`PUT /api/v1/admin/metadata/settings` for managing online metadata provider API keys. TMDB and Google Books keys are stored in the database, override environment values, and are picked up by the worker without a restart. `HEARTH_GOOGLE_BOOKS_API_KEY` adds Google Books API-key support.

### Changed
- Converted HTTP middlewares (`RequestSizeLimitMiddleware`, `RequestIDMiddleware`, `SecurityHeadersMiddleware`) to pure ASGI middleware to remove context-switch and streaming wrapper overhead.
- Optimized `GET /items/{id}` ETags to derive from item and watch state timestamps, short-circuiting 304 Not Modified responses before model detail generation.
- Optimized `GET /home/recently-added` to evaluate deduplicated lightweight scalar tuples before loading full ORM models and selectin relationships.
- Reused application-state `ImagePipeline` singleton across image requests and moved blurhash computation to a worker thread to eliminate IPC overhead.
- Scoped book folder discovery queries to the active directory prefix.
- Updated web client home page to fetch all shelves in a single request, eliminating client-side API waterfalls.
- Dynamically split `JASSUB` in the video player route, loading WebAssembly assets only when ASS/SSA subtitles are rendered.

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
