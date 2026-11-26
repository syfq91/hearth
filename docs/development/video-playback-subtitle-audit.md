# Video Playback & Subtitle Syncing Performance Audit

**Scope:** Video loading, HLS/direct-play streaming, transcode session management, and subtitle delivery/syncing across backend (`app/`) and frontend (`web/`).  
**Severity:** 🔴 Critical · 🟠 High · 🟡 Medium · 🔵 Low

---

## Executive Summary

Hearth's video playback stack already implements several strong performance practices:
- **Scan-time probing:** `POST /api/v1/items/{item_id}/playback-info` reads persisted `MediaFile.streams_json` rather than running `ffprobe` per playback request.
- **Short opening HLS segment:** Transcodes use `transcode_first_segment_duration_seconds` with forced keyframes (`Build` in `app/services/transcode/builder.py`) so playback starts before a full 4s segment finishes encoding.
- **Single-pass embedded subtitle prewarming:** `playback_info` kicks off `ensure_embedded_subtitles()` to extract all text subtitle tracks in one `ffmpeg` pass into a revision-keyed cache.
- **RVFC-aware JASSUB rendering:** `VideoPlayer.svelte` avoids clobbering JASSUB's `requestVideoFrameCallback` loop during active playback, and initializes JASSUB with `EMPTY_ASS` in parallel with downloading the subtitle document.

However, several bottlenecks and bugs degrade **video startup latency**, **memory stability**, **seek/tab-switch resilience**, and **subtitle sync responsiveness**.

**Top 5 Issues to Address First:**
1. 🔴 **`startup_sweep()` wipes subtitle and font caches on every restart** (`app/services/transcode/session.py`).
2. 🔴 **`visibilitychange` kills active HLS transcode sessions on tab switch** (`web/src/lib/components/VideoPlayer.svelte`).
3. 🟠 **`VideoPlayer.svelte` blocks `videoEl.play()` up to 2s waiting on subtitle initialization** (`web/src/lib/components/VideoPlayer.svelte`).
4. 🟠 **Native WebVTT subtitle sync adjustment re-fetches and re-parses the entire file from the server** (`web/src/lib/player/subtitles.svelte.ts`, `app/api/v1/subtitles.py`).
5. 🟠 **`SessionManager._lock` is held across font extraction, `ffmpeg` spawn, and `stop()`/`rmtree()`** (`app/services/transcode/session.py`).

---

## 1. Video Loading & Streaming Performance

### 1.1 🔴 `SessionManager.startup_sweep()` Wipes Persistent Subtitle & Font Caches
**Location:** [session.py](file:///home/syafiq/code/hearth/app/services/transcode/session.py#L816-L829)

`startup_sweep()` iterates over every directory in `settings.transcode_dir` and deletes it with `shutil.rmtree()`. However, both the embedded subtitle cache (`transcode_dir / "subtitles"`, used in [playback.py](file:///home/syafiq/code/hearth/app/api/v1/playback.py#L723) and [subtitles.py](file:///home/syafiq/code/hearth/app/api/v1/subtitles.py#L115)) and the container font cache (`transcode_dir / "fonts"`, used in [session.py](file:///home/syafiq/code/hearth/app/services/transcode/session.py#L347)) live inside `transcode_dir`.

- **Impact:** Every API restart destroys all cached embedded subtitles and extracted fonts, forcing expensive full-file `ffmpeg` demuxes on the next playback.
- **Improvement:** Skip `"subtitles"` and `"fonts"` in `startup_sweep()`, or move persistent caches under `settings.data_dir / "cache"`.

---

### 1.2 🔴 `visibilitychange` Kills Active HLS Transcodes on Tab Switch
**Location:** [VideoPlayer.svelte](file:///home/syafiq/code/hearth/web/src/lib/components/VideoPlayer.svelte#L972-L985)

```ts
const onHide = () => {
    void report('paused');
    if (sessionId) {
        sendStopBeacon(sessionId, sessionMediaToken);
    }
};
window.addEventListener('pagehide', onHide);
document.addEventListener('visibilitychange', onHide);
```

`onHide` does not check `document.visibilityState === 'hidden'`, and worse, it fires `sendStopBeacon()` on `visibilitychange`. Switching to another browser tab or minimizing the window immediately terminates the backend `ffmpeg` transcode session and deletes its output directory.

- **Impact:** Returning to the tab results in `404 session_not_found` on the next segment fetch, forcing a full reload/re-negotiation.
- **Improvement:** Only call `sendStopBeacon()` on `pagehide` (or component unmount). On `visibilitychange` when `document.visibilityState === 'hidden'`, only report `'paused'` (or pause playback) without destroying the transcode session.

---

### 1.3 🟠 `VideoPlayer` Blocks `videoEl.play()` Up to 2 Seconds Waiting on Subtitles
**Location:** [VideoPlayer.svelte](file:///home/syafiq/code/hearth/web/src/lib/components/VideoPlayer.svelte#L766-L778)

`attachSource()` awaits `Promise.race([subReady, new Promise((r) => setTimeout(r, 2000))])` before setting `loading = false` and calling `videoEl.play()`.

- **Impact:** When subtitles are enabled (especially ASS/SSA requiring dynamic `import('jassub')` + WASM startup + embedded subtitle extraction), video playback startup is artificially delayed by up to 2,000 ms even when the video stream is already ready.
- **Improvement:** Call `videoEl.play()` and clear `loading` immediately after attaching the video source, and let `applySubtitleChoice()` complete asynchronously in parallel.

---

### 1.4 🟠 `SessionManager._lock` Held Across Subprocess Spawn, Font Extraction, and Teardown
**Location:** [session.py](file:///home/syafiq/code/hearth/app/services/transcode/session.py#L718-L806)

`SessionManager.create_session()` holds `self._lock` while calling `await session.start_process()`, which itself awaits `_prepare_burn_in_fonts()` (can spawn `ffmpeg` to extract fonts) and `asyncio.create_subprocess_exec()`. Likewise, `stop_session()`, `stop_user_sessions()`, and `reap_idle_sessions()` hold `self._lock` while awaiting `session.stop()` (up to 3s `process.terminate()` wait + `shutil.rmtree()`).

- **Impact:** One slow session startup, font extraction, or stuck `ffmpeg` termination blocks all other users from starting or stopping transcode sessions.
- **Improvement:** Reserve the concurrency slot and register the session in `self.sessions` under `self._lock`, then release `self._lock` before running `_prepare_burn_in_fonts()`, `start_process()`, or `session.stop()`.

---

### 1.5 🟡 Polling Loops & Synchronous Filesystem I/O in `TranscodeSession` and `hls.py`
**Locations:**
- [session.py](file:///home/syafiq/code/hearth/app/services/transcode/session.py#L491-L605): `wait_for_first_segment()` polls every `100 ms`; `wait_for_segment_ready()` and `ensure_segment()` poll every `50 ms`. `ensure_segment()` also calls `self.output_dir.glob("seg_*.m4s")` synchronously on the event loop inside `_seek_lock`.
- [session.py](file:///home/syafiq/code/hearth/app/services/transcode/session.py#L256-L263): `encoded_time_seconds` loops `Path.exists()` synchronously on the event loop during `throttle_sessions()` and segment serving.
- [hls.py](file:///home/syafiq/code/hearth/app/api/v1/hls.py#L274): `get_media_playlist()` calls `m3u8_path.read_text()` synchronously even when `session.duration_seconds > 0` (where `raw_content` is discarded and the VOD playlist is generated in memory).
- [hls.py](file:///home/syafiq/code/hearth/app/api/v1/hls.py#L470-L583): `init_path.exists()`, `init_path.stat()`, `seg_path.exists()`, `seg_path.stat()` run synchronously on the event loop.

- **Impact:** Adds 50–100 ms polling latency on segment boundaries and risks event-loop stalls if `transcode_dir` is on slow disk/network storage.
- **Improvement:**
  - Skip `m3u8_path.read_text()` in `get_media_playlist()` when `session.duration_seconds > 0`.
  - Offload `glob()`, `stat()`, `exists()`, and `shutil.rmtree()` to `asyncio.to_thread` (or track generated segments in memory / via an `asyncio.Condition`).

---

### 1.6 🟡 Unbounded `backBufferLength: Infinity` & Uncancelled Retry Timers in `HlsEngine`
**Locations:**
- [hls-engine.ts](file:///home/syafiq/code/hearth/web/src/lib/player/hls-engine.ts#L96): `backBufferLength: Infinity` retains all previously played segments in MSE `SourceBuffer`.
- [video-resilience.ts](file:///home/syafiq/code/hearth/web/src/lib/video-resilience.ts#L102-L104): `HlsRetryLoader` schedules `setTimeout(() => super.load(...), delayMs)` on `503` responses without cancelling the timer if `abort()` or `destroy()` is called.

- **Impact:** On long movies or memory-limited Smart TVs (ES2019 target), `backBufferLength: Infinity` can trigger MSE `QuotaExceededError` or tab OOM. Uncancelled retry timers can fire `super.load()` after the player is destroyed.
- **Improvement:** Set `backBufferLength: isHighBitrate ? 10 : 60`, and track/clear pending `setTimeout` IDs in `HlsRetryLoader.abort()` / `destroy()`.

---

### 1.7 🟡 Sequential Watch Route Waterfall & Backward-Seek Progress Blind Spot
**Locations:**
- [watch/[itemId]/+page.ts](file:///home/syafiq/code/hearth/web/src/routes/watch/[itemId]/+page.ts#L10-L20): For `series` items, fetches `/items/{id}` → `/series/{id}/next-up` → `/items/{nextUp.id}` sequentially (3 RTTs) before `VideoPlayer` mounts and sends `POST /items/{id}/playback-info`.
- [VideoPlayer.svelte](file:///home/syafiq/code/hearth/web/src/lib/components/VideoPlayer.svelte#L268,L1050-L1054): `lastReportedMs` only moves forward (`Math.max(lastReportedMs, position_ms)`), and the 10s periodic report checks `positionMs - lastReportedMs >= REPORT_INTERVAL_MS`.

- **Impact:** Series playback startup incurs an extra 2 RTTs before `playback-info` begins; backward seeks suppress periodic progress reporting until playback passes the previous furthest timestamp.
- **Improvement:** Track `lastReportWallTimeMs = Date.now()` for the 10s reporting interval instead of comparing against `lastReportedMs`.

---

### 1.8 🔵 Direct-Play Streaming Sync `stat()` Check
**Location:** [stream.py](file:///home/syafiq/code/hearth/app/api/v1/stream.py#L273-L278,L388)

`path.stat()` and `path.is_file()` execute synchronously on the event loop before `asyncio.to_thread` is used for reading bytes.

- **Improvement:** Wrap `stat()`/`is_file()` in `asyncio.to_thread` so slow/stale network mounts cannot block other concurrent requests.

---

## 2. Subtitle Syncing & Delivery Performance

### 2.1 🟠 Native WebVTT Sync Offset Forces Server Re-Fetch & DOM `<track>` Remount
**Locations:**
- [subtitles.svelte.ts](file:///home/syafiq/code/hearth/web/src/lib/player/subtitles.svelte.ts#L100-L134)
- [subtitles.py](file:///home/syafiq/code/hearth/app/api/v1/subtitles.py#L157-L170,L210-L276)

When adjusting subtitle timing via `SubtitleSyncControl.svelte`:
- **ASS/SSA (JASSUB):** Updates `jassubInstance.timeOffset` in memory and repaints immediately (0 network cost, <1 ms).
- **WebVTT / SRT (`<track>`):** `SubtitleManager.setOffset()` debounces 200 ms, then calls `loadTrack()` which tears down the `<track>` element and re-fetches the entire subtitle file from the backend with `?shift_ms=...`. On the backend, `shift_webvtt()` re-parses and rewrites every timestamp line on every request, with `Cache-Control: private, max-age=0, must-revalidate`.

- **Impact:** Scrubbing or stepping the sync slider on SRT/VTT subtitles causes repeated network round-trips, server CPU work, and visible subtitle flicker when `<track>` is removed and re-added.
- **Improvement:** Shift `VTTCue.startTime` and `VTTCue.endTime` directly on `activeTrackElement.track.cues` in the browser (storing original unshifted cue timings on load). This makes WebVTT sync calibration instantaneous and offline-capable, matching JASSUB.

---

### 2.2 🟠 Double Fetch of Native Subtitles When `mediaStartSeconds` Settles
**Location:** [VideoPlayer.svelte](file:///home/syafiq/code/hearth/web/src/lib/components/VideoPlayer.svelte#L729,L766), [subtitles.svelte.ts](file:///home/syafiq/code/hearth/web/src/lib/player/subtitles.svelte.ts#L74-L84)

`SubtitleManager` applies `mediaStartSeconds` to native tracks via `shift_ms` in `nativeTrackUrl()`. If `setMediaStartSeconds()` is called with a new non-zero container start PTS while a native track is already active (e.g., across restarts or mode switches), `scheduleOffsetRemount()` schedules another full track re-fetch.

- **Improvement:** Fixing §2.1 (client-side `VTTCue` shifting) eliminates this extra network fetch as well, since `mediaStartSeconds` can be applied to `track.cues` in memory.

---

### 2.3 🟡 HLS Subtitle Extraction Races With Prewarm Cache & Lacks Lock
**Locations:**
- [hls.py](file:///home/syafiq/code/hearth/app/api/v1/hls.py#L395-L421)
- [subtitles.py](file:///home/syafiq/code/hearth/app/services/subtitles.py#L335,L413-L450)

In `get_subtitle_vtt()`, if `subtitles.vtt` is not in `session.output_dir` and not yet in the prewarmed cache, it calls `extract_embedded_subtitle()` directly without acquiring `_EMBEDDED_CACHE_LOCKS`, racing with `_prewarm_embedded_subtitles()`. Additionally:
- `_EMBEDDED_CACHE_LOCKS` in `app/services/subtitles.py` is an unbounded `dict[Path, LoopAwareLock]` that is never pruned.
- `ensure_embedded_subtitles()` performs synchronous `is_file()`, `mkdir()`, `stat()`, and `replace()` calls on the event loop.

- **Impact:** On HLS playback with embedded subtitles, two `ffmpeg` demux processes can run concurrently for the same file.
- **Improvement:** Route `get_subtitle_vtt()` through `ensure_embedded_subtitles()` (using the shared lock) instead of spawning a separate uncoordinated `extract_embedded_subtitle()` process, and bound/clean up `_EMBEDDED_CACHE_LOCKS` entries after extraction completes.

---

### 2.4 🟡 Synchronous Sidecar Discovery & Missing `AbortController` on Subtitle Fetch
**Locations:**
- [subtitles.py](file:///home/syafiq/code/hearth/app/api/v1/subtitles.py#L232): `discover_subtitles(video)` runs synchronously on the event loop for every sidecar subtitle request.
- [subtitles.svelte.ts](file:///home/syafiq/code/hearth/web/src/lib/player/subtitles.svelte.ts#L170): `fetch(fetchUrl)` for ASS/SSA tracks has no `AbortController`, so superseded track switches still download the full file before discarding it.

- **Improvement:** Run `discover_subtitles()` via `asyncio.to_thread`, and pass an `AbortController.signal` to `fetch()` in `SubtitleManager.mount()`, aborting it in `detach()`.

---

### 2.5 🔵 Global `_STATE_LOCK` Serializes All Playback Progress Writes
**Location:** [playback.py](file:///home/syafiq/code/hearth/app/api/v1/playback.py#L272,L704)

*(Previously noted in `docs/development/performance-audit.md` §7.6; still open.)* A single module-level `_STATE_LOCK` serializes all `/progress`, `/watched`, `/favorite`, and `/rating` writes across all users and items.

- **Improvement:** Scope the lock per `(user_id, item_id)` or use atomic SQLite `INSERT ... ON CONFLICT DO UPDATE` for single-row progress updates.

---

## 3. Summary & Recommended Priority Order

| Priority | Severity | Area | Issue | Effort |
|---|---|---|---|---|
| 1 | 🔴 Critical | Backend | `startup_sweep()` deletes `subtitles/` and `fonts/` caches on restart | Quick win |
| 2 | 🔴 Critical | Frontend | `visibilitychange` fires `sendStopBeacon()` and kills active transcodes on tab switch | Quick win |
| 3 | 🟠 High | Frontend | `attachSource()` blocks `videoEl.play()` up to 2s waiting on subtitle load | Quick win |
| 4 | 🟠 High | Frontend | Native WebVTT sync offset re-fetches from server instead of shifting `VTTCue` in memory | Medium |
| 5 | 🟠 High | Backend | `SessionManager._lock` held across `ffmpeg` spawn, font extraction, and `stop()` | Medium |
| 6 | 🟡 Medium | Frontend | `HlsEngine` `backBufferLength: Infinity` + uncancelled 503 retry timers | Quick win |
| 7 | 🟡 Medium | Frontend | Backward seek suppresses 10s periodic progress reporting | Quick win |
| 8 | 🟡 Medium | Backend | `hls.py` `get_subtitle_vtt()` races with `_prewarm_embedded_subtitles()` | Quick win |
| 9 | 🟡 Medium | Backend | Segment polling (`50-100ms`) & sync `glob`/`stat`/`read_text` on event loop | Medium |
| 10 | 🔵 Low | Backend | Direct-play `stream.py` sync `stat()` & global `_STATE_LOCK` in `playback.py` | Medium |
