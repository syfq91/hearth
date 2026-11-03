# Hearth 1.0.0 Release Acceptance Checklist

This checklist documents the full manual and automated verification required before tagging a release of Hearth.

**Target Version:** `1.0.0`  
**Date:** 2026-11-03  
**Status:** Complete (Automated & Environment-Verified; Physical Hardware Items Noted)

---

## 1. Fresh Installation & Infrastructure

- [x] **Pristine Container Deployment**: Starts cleanly with empty `/config` and `/transcode` volumes via `docker compose up -d`.
- [x] **Advisory Migration Lock**: Initial database created and upgraded to latest schema revision without race conditions.
- [x] **Container Hardening**:
  - [x] Runs unprivileged under non-root UID 1000 and GID 1000.
  - [x] Read-only root filesystem (`read_only: true`).
  - [x] All Linux capabilities dropped (`cap_drop: ALL`).
  - [x] `no-new-privileges: true` enforced.
- [x] **Container Healthcheck**: `GET /api/v1/health` reports status `"ok"`, database `"connected"`, and migrations `"up_to_date"`.

---

## 2. Setup & Authentication

- [x] **First-Run Setup**: First administrator account created via `/setup` wizard or `POST /api/v1/auth/setup`.
- [x] **Setup Idempotence**: Subsequent setup attempts return HTTP 409 Conflict.
- [x] **Authentication Flow**:
  - [x] Login (`POST /api/v1/auth/login`) returns access token and rotating refresh token in HTTP-only cookie.
  - [x] Access token renewal (`POST /api/v1/auth/refresh`) works seamlessly.
  - [x] Password change or admin revocation bumps `token_version` and invalidates live tokens.
- [x] **Media Tokens**: Opaque short-lived query tokens (`?token=...`) generated for media playback and images, strictly isolated from API routes.

---

## 3. Real Library Verification (All Media Types)

### A. Movies
- [x] Library creation for movie path.
- [x] Scan and identification via embedded `.nfo` or TMDB API.
- [x] High-resolution poster and backdrop extraction and resizing.
- [x] Movie detail page rendering with overview, duration, release year, genres, and cast.
- [x] Playback start, byte-range seeking, watch progress reporting, and continue-watching shelf placement.

### B. Television Series
- [x] Multi-season hierarchy scanning (`Series` -> `Season` -> `Episode`).
- [x] Season picker and episode list navigation.
- [x] "Next up" episode auto-advancement when an episode finishes.
- [x] Episode progress saved and resumed across browser sessions.

### C. Music
- [x] ID3 / Vorbis tag scanning (FLAC, MP3, AAC, Opus).
- [x] Disc and track numbering, album artwork extraction.
- [x] Artist index, artist detail page, and disc-separated album view.
- [x] Continuous queue playback with persistent audio bar.
- [x] Media Session API lockscreen and system media key controls.
- [x] Background scrobbling to ListenBrainz with encrypted user token.

### D. Books & Comics
- [x] EPUB parsing (OPF, metadata, chapter hierarchy) and in-browser reading via foliate-js.
- [x] PDF parsing (metadata, canvas rendering) via pdf.js with page navigation.
- [x] CBZ/CBR comic archive reading with server-side on-demand page extraction, resizing, and 5-page prefetch.
- [x] Reading progress synchronization (EPUB CFI, PDF page, Comic page) across devices.

---

## 4. Hardware Transcoding & Playback

- [x] **Direct Play**: Container and codec match client capability profile without transcoding; byte ranges served efficiently.
- [x] **Software Transcoding Fallback**: Systems without `/dev/dri` transcode cleanly using `libx264 veryfast` without hangs.
- [x] **Intel QuickSync (VA-API)**:
  - [x] Render node `/dev/dri/renderD128` detected.
  - [x] Driver verified via `vainfo` (`Intel iHD driver`).
  - [x] 4K HEVC HDR10 downscaled and tonemapped to 1080p H.264 SDR at > 1.5x speed.
- [ ] **AMD Radeon / Ryzen APU (VA-API)**:
  - Driver configuration provided (`compose.amd.yaml` with `/dev/kfd` and `radeonsi`).
  - *Hardware-dependent: Requires physical AMD GPU hardware for runtime fps verification (Flagged for human manual verification).*
- [x] **Session Reaper & Concurrency**:
  - [x] Idle HLS transcode sessions terminated after 60s of inactivity.
  - [x] Concurrency ceiling returns clean HTTP 503 (`Retry-After: 5`) instead of CPU/memory starvation.
  - [x] Mid-stream seek re-segments accurately without orphan ffmpeg processes.

---

## 5. Security & Resilience

- [x] **Dependency Security**: `pip-audit` reports zero vulnerabilities across all dependencies.
- [x] **Path Traversal Protection**: Symlink resolution and directory escaping rejected on all media and HLS endpoints.
- [x] **Token Scope Separation**: Media tokens cannot access REST or Admin API endpoints.
- [x] **SSRF Defense**: External artwork downloading strictly restricted to allowlisted provider domains.
- [x] **Defensive Headers**: `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: strict-origin-when-cross-origin` on all responses.
- [x] **Secret Redacting**: Passwords, tokens, and authorization headers redacted in JSON logs and ring-buffer log viewer.
- [x] **Crash Recovery**: Server restart during active scan/stream requeues in-flight jobs and flushes orphan transcode files.
- [x] **Disk Full Safeguard**: Creation of new transcode sessions refused when disk free space is < 50MB.
- [x] **SQLite Concurrency**: High-concurrency interleaved writes (20 concurrent workers) complete without lock errors.

---

## 6. Observability & Operations

- [x] **Prometheus Metrics**: `GET /metrics` serves standard Prometheus format when enabled (`metrics_enabled=True`) and 404 when disabled.
- [x] **Admin Log Viewer**: Filter logs by level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) and search by keyword.
- [x] **Online Backup**: Atomic database snapshot via `POST /api/v1/admin/backup` and `hearth backup` CLI.
- [x] **Disaster Restore**: Backup restoration into fresh container restores catalog and user states identically.
- [x] **Documentation**:
  - [x] Quick start in `README.md`.
  - [x] `docs/HARDWARE_TRANSCODING.md`.
  - [x] `docs/REVERSE_PROXY.md` with Caddy, Traefik, and Nginx.
  - [x] `docs/BACKUP_AND_UPGRADE.md`.
  - [x] `docs/TROUBLESHOOTING.md`.

---

## 7. Web Application & 10-Foot TV Mode

- [x] **Responsive Layout**: Validated across mobile, tablet, and desktop viewports.
- [x] **10-Foot TV Mode**: Spatial focus navigation (`actions/spatial-focus.ts`) handles Arrow and Enter keys for D-pad remote browsing.
- [x] **Accessibility**: Visible focus rings, keyboard controls, skip links, aria-labels, and reduced-motion support.
- [x] **Lighthouse Performance Score**: Scored **100** on authenticated home page (target: >= 80).
- [x] **Playwright E2E Suite**: 9/9 specs green in CI against Docker image (`first-run`, `auth`, `video`, `music`, `books`).

---

## 8. Release Sign-Off Summary

- **Automated Verification:** 100% of integration, resilience, security, migration, and unit tests passing.
- **Physical Hardware Items:**
  - Intel QuickSync VA-API: Verified on host `/dev/dri/renderD128` (documented in `docs/HARDWARE_TESTS.md`).
  - AMD Radeon VA-API: Configuration in place in `docker/compose.amd.yaml`; requires physical AMD machine for real encode verification.
- **High-Severity Issues:** None open.
- **Conclusion:** Ready for `1.0.0` release.
