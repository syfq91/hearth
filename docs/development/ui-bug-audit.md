# Web UI bug audit

**Date:** 2026-11-23
**Scope:** `web/src` (all 136 files — SvelteKit routes, Svelte components, and lib/state modules), with cross-references into `packages/client/src/schema.d.ts`, `web/node_modules` (hls.js 1.7.4, pdfjs-dist 6.4.299), and the FastAPI API where needed.
**Method:** Manual code review in three parallel passes (routes, components, lib logic), followed by direct verification of every High- and most Medium-severity findings against the source and the installed library implementations.
**Baseline:** `npm run check` → 0 errors, 0 warnings. `npm run test:unit` → 88/88 pass. All findings below are logic bugs that static checks and the current test suite do not catch.

---

## 1. Executive summary

31 concrete bugs were found: **4 High**, **15 Medium**, **12 Low**.

| ID | Finding | Severity | Primary location |
| --- | --- | --- | --- |
| UI-001 | PDF pages never render — `render()` omits required `viewport` | High | `lib/components/PdfReader.svelte:22-28,102-106` |
| UI-002 | HLS 503 retry re-calls hls.js's single-use `load()` — retry always throws | High | `lib/video-resilience.ts:102-104` |
| UI-003 | `visibilitychange` tears down the active HLS session in both directions | High | `lib/components/VideoPlayer.svelte:945-959` |
| UI-004 | `invalidateAll()` never clears the API GET cache — admin mutations and polling serve stale data | High | `lib/api/client.ts:19-25,208-215` (+ loaders) |
| UI-005 | `startPlayback()` runs twice on every item change | Medium | `lib/components/VideoPlayer.svelte:871-908` |
| UI-006 | Final `report('stopped')` on teardown is always a no-op | Medium | `lib/components/VideoPlayer.svelte:962-966,243` |
| UI-007 | Global keydown handler breaks keyboard activation of buttons/dialogs | Medium | `lib/components/VideoPlayer.svelte:428-488` |
| UI-008 | Arrow keys on the seekbar seek 15s instead of 5s | Medium | `lib/components/VideoPlayer.svelte:1247-1250,441-450` |
| UI-009 | HLS recovery-attempt counters are never reset after success | Medium | `lib/player/hls-engine.ts:72-73,167-189` |
| UI-010 | Progress throttling mixes content time and file time | Medium | `lib/components/VideoPlayer.svelte:242-250,1024-1028` |
| UI-011 | Per-item subtitle sync offset is not reloaded on item change | Medium | `lib/components/VideoPlayer.svelte:689-710` |
| UI-012 | PDF "Next" visually shows the previous page (`scrollTop = 0`) | Medium | `lib/components/PdfReader.svelte:70-120` |
| UI-013 | Favorite/watched overrides leak across item navigation | Medium | `lib/components/ItemDetail.svelte:17-20` |
| UI-014 | Search debounce timer has no cleanup — leaving the page navigates back | Medium | `routes/search/+page.svelte:27-43` |
| UI-015 | Songs/genres pagination uses a stale search term after "clear" | Medium | `routes/music/songs/+page.svelte:15,63-72` |
| UI-016 | Reader hangs on "Preparing reader…" if token minting fails | Medium | `routes/books/[bookId]/read/+page.svelte:155-165` |
| UI-017 | Deleting a library leaves it in the header navigation | Medium | `routes/admin/libraries/+page.svelte:179-189` |
| UI-018 | Editing your own account leaves the session stale | Medium | `routes/admin/users/+page.svelte:74-94` |
| UI-019 | Up-next prompt can be shown for the wrong episode after fast navigation | Medium | `routes/watch/[itemId]/+page.svelte:33-50` |
| UI-020 | TV-mode UA detection skipped when `localStorage` is absent | Low | `lib/tv.svelte.ts:20-21` |
| UI-021 | Search input does not follow external `q` changes | Low | `routes/search/+page.svelte:22-23` |
| UI-022 | Next-episode lookup falls back to the first season when the current season is missing | Low | `lib/api/next-episode.ts:43` |
| UI-023 | Series "Play" can link to `/watch/<seriesId>` (not an item) | Low | `routes/series/[seriesId]/+page.svelte:55-60` |
| UI-024 | Stopping a transcode session leaves it displayed as active | Low | `routes/admin/transcoding/+page.svelte:50-58` |
| UI-025 | Resolved audio stream URLs are not invalidated on logout/token refresh | Low | `lib/player.svelte.ts:68`, `lib/media-token.svelte.ts:52` |
| UI-026 | Unhandled rejection on artwork/file token minting failure | Low | `lib/components/Poster.svelte:46-48`, `BookCover.svelte:23-25` |
| UI-027 | Gapless audio preloader is never cleaned up | Low | `lib/components/AudioBar.svelte:94-108` |
| UI-028 | Rapid next/previous can desync `player.src` from `player.track` | Low | `lib/player.svelte.ts:93-106` |
| UI-029 | Shelf scroll-arrow state goes stale when `items` changes | Low | `lib/components/Shelf.svelte:45-60` |
| UI-030 | Folder-browser navigation can be overwritten by a stale response | Low | `lib/components/FolderBrowserModal.svelte:23-47` |
| UI-031 | Modal auto-close timer and image probes are not cleaned up | Low | `lib/components/PluginConfigModal.svelte:121-123`, `ComicReader.svelte:99-111` |

---

## 2. High severity

### UI-001 — PDF pages never render

**Location:** `web/src/lib/components/PdfReader.svelte:22-28` (local `PdfPage.render` type), `:89`, `:102-106`.

**Problem.** The component computes a viewport on line 89 but never passes it to `page.render()`:

```ts
const viewport = page.getViewport({ scale: 1 });
// ...
await page.render({
    canvasContext: context,
    canvas,
    transform: dpr === 1 ? [] : [dpr, 0, 0, dpr, 0, 0]
}).promise;
```

In the installed `pdfjs-dist` 6.4.299, `PDFPageProxy.render()` destructures `viewport` with no default (`node_modules/pdfjs-dist/build/pdf.mjs:15829-15845`), and `CanvasGraphics.beginDrawing()` unconditionally executes `this.ctx.transform(...viewport.transform)` (`pdf.mjs:10951`). `transform: []` is also invalid when `dpr === 1` — pdf.js treats it as truthy and sets `outputScaleX/Y` to `undefined`.

**Impact.** Every PDF book renders an empty `.pages` area. The render promise rejects, `container.append(canvas)` (line 111) is never reached, and the rejection escapes into a `void` expression in the `$effect` at line 119.

**Suggested fix.** Pass the viewport and use pdf.js's supported scaling convention, e.g. compute `page.getViewport({ scale: dpr })` and call `page.render({ canvasContext: context, canvas, viewport })`, removing the hand-rolled `transform`.

---

### UI-002 — HLS 503 retry always throws

**Location:** `web/src/lib/video-resilience.ts:102-104`, verified against `web/node_modules/hls.js/src/utils/base-loader.ts:29-43`.

**Problem.** `createHlsRetryLoaderClass` wraps `Hls.DefaultConfig.loader`. When a 503 arrives, the wrapper schedules a retry by calling `super.load()` a **second time on the same loader instance**:

```ts
setTimeout(() => {
    super.load(context, config, wrappedCallbacks);
}, delayMs);
return;
```

hls.js's base class enforces single use:

```ts
load(context, config, callbacks) {
  const stats = this.stats;
  if (stats.loading.start) {
    throw new Error('Loader can only be used once.');
  }
  ...
}
```

The first `super.load()` already set `stats.loading.start`. The retry throws inside a `setTimeout` (uncaught). The wrapper also `return`s without calling `originalOnError`, so hls.js's error controller never learns about the 503.

**Impact.** The Retry-After mechanism never retries and never reports a fatal error. Playback stalls silently on exactly the transient 503s (transcode cold start / circuit-breaker cooldown) this code exists to handle.

**Why tests miss it.** `video-resilience.test.ts` uses a `MockBaseLoader` with no single-use guard, so the second `load()` succeeds in the test.

**Suggested fix.** Retry with a fresh loader instance (`new BaseLoader().load(...)`), or avoid re-invoking `load()` by retrying the underlying XHR/fetch inside a single load, and always forward non-retryable errors to `originalOnError`.

---

### UI-003 — `visibilitychange` stops the active HLS session in both directions

**Location:** `web/src/lib/components/VideoPlayer.svelte:945-959`.

**Problem.**

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

`visibilitychange` fires both when the tab becomes hidden **and** when it becomes visible again. `sendStopBeacon()` POSTs to the HLS session stop endpoint, which tears down the transcode session the still-playing `<video>` depends on.

**Impact.** Switching tabs (or returning to the tab) kills playback and writes `state: 'paused'` progress while playback continues.

**Suggested fix.** Split the handlers: keep `pagehide` unconditional, but in the `visibilitychange` handler guard with `if (document.visibilityState !== 'hidden') return;`. Consider only stopping on `hidden` and reporting a resume on `visible`.

---

### UI-004 — `invalidateAll()` never clears the API GET cache

**Location (root cause):** `web/src/lib/api/client.ts:19-25, 208-215`. The 30-second `getCache` is cleared only on access-token change (`setAccessToken`, line 104) and via explicit `clearApiCache()` — never on mutations or SvelteKit invalidation.

**Affected flows:**

| Flow | Location | Symptom |
| --- | --- | --- |
| Plugin enable/disable/reload | `routes/admin/plugins/+page.svelte:53-83`, loader `+page.ts:7` | Status snaps back to the pre-mutation value |
| Plugin config save | `routes/admin/plugins/+page.svelte:90-98` | Re-GET at line 93 returns the same stale cached list |
| Live server logs | `routes/admin/logs/+page.svelte:57-85,175-181` | 3s poll is a no-op for 30s; manual Refresh is a no-op |
| User create/edit | `routes/admin/users/+page.svelte:50,88`, loader `+page.ts:6-8` | New/edited users absent for 30s |
| Scan all libraries | `routes/admin/+page.svelte:18-20`, loader `+page.ts:12-18` | "Recent jobs" card shows no queued scans |
| Rematch | `routes/admin/unmatched/+page.svelte:98-105`, loader `+page.ts:6-11` | $effect can repopulate the stale list |

**Impact.** Admin actions appear to do nothing or to be reverted; the logs page's core real-time behavior is broken for the first 30 seconds of every cache window. Note that `admin/libraries/+page.ts` already documents and applies the correct mitigation (`cache: false`) for its own loaders.

**Suggested fix.** Systemic: call `clearApiCache()` from `request()` for every non-GET method after a successful response, so any mutation invalidates cached reads. Additionally, pass `{ cache: false }` on polling/high-churn endpoints such as `/admin/logs`. A narrower alternative is to clear the cache inside a shared `invalidateAll()` wrapper used by all mutation handlers.

---

## 3. Medium severity

### UI-005 — `startPlayback()` runs twice on every item change
**Location:** `VideoPlayer.svelte:871-879` and `:881-908`; `startPlayback` at `:584-633`.
The first effect calls `startPlayback()` whenever `videoEl && !info && !loadError`. The second effect detects an item change, sets `info = null` (line 895) and calls `startPlayback()` itself (line 899). That write re-invalidates the first effect, which calls it again. Two `POST /items/{id}/playback-info` calls create two HLS sessions, only the last-attached one gets a stop beacon, and out-of-order responses can attach a stale source. This occurs on every "play next episode" transition because the component stays mounted across `goto`. Remove the call from one of the two effects.

### UI-006 — Final `report('stopped')` on teardown is always a no-op
**Location:** `VideoPlayer.svelte:962-966` and `:242-243`.
Teardown sets `destroyed = true` and then calls `report('stopped')`, but `report()` begins with `if (!videoEl || destroyed) return;`. Client-side navigation away mid-playback therefore never persists the final watch position. Report before setting `destroyed`, or add a `force` parameter that bypasses the guard.

### UI-007 — Global keydown handler breaks keyboard activation of buttons/dialogs
**Location:** `VideoPlayer.svelte:428-488`; `<svelte:window onkeydown={handleKeydown} />`.
Only text inputs are exempted (line 430-433). Space on a focused button — e.g. the up-next dialog's "Play next episode" button, which the watch page focuses — calls `preventDefault()` and toggles playback instead of activating the button. Exempt `button`, `a`, `[role="dialog"]`, and other interactive targets (or check `event.target.closest(...)`).

### UI-008 — Arrow keys on the seekbar seek 15s instead of 5s
**Location:** `VideoPlayer.svelte:1247-1250` and `:441-450`.
The seekbar's own `onkeydown` calls `seekBy(±5)` without `preventDefault()`/`stopPropagation()`. The event bubbles to the window handler, which sees a `div` target and calls `seekBy(±10)`. Add `e.preventDefault(); e.stopPropagation();` in the seekbar handler.

### UI-009 — HLS recovery-attempt counters are never reset
**Location:** `lib/player/hls-engine.ts:72-73, 167-189`.
`networkRecoveryAttempts` and `mediaRecoveryAttempts` increment on each fatal error but are never reset after a successful recovery. Three transient errors early in a session permanently disable automatic recovery; the fourth calls `options.onError` without attempting `startLoad()`/`recoverMediaError()`. Reset the relevant counter on a success signal such as `FRAG_LOADED` or `BUFFER_APPENDED`.

### UI-010 — Progress throttling mixes content time and file time
**Location:** `VideoPlayer.svelte:242-250` and `:1024-1028`.
`ontimeupdate` computes `positionMs` from content time (`displayTime(videoEl.currentTime)`, offset subtracted), while `report()` throttles against raw `videoEl.currentTime` in `lastReportedMs`. For a file with a container start offset, reports fire only every `offset + 10s` (e.g. every 70s for a 60s offset), so watch progress and resume points lag badly. Use the same time base for both the position and the throttle comparison.

### UI-011 — Per-item subtitle sync offset is not reloaded on item change
**Location:** `VideoPlayer.svelte:689-710`.
The `hearth_sub_offset_${itemId}` localStorage read happens only inside `if (!subtitleManager)`, i.e. once per mount. Because VideoPlayer stays mounted across `goto('/watch/<next-id>')`, the new episode inherits the previous episode's offset and `currentSubOffset`. Re-read the offset when `itemId` changes.

### UI-012 — PDF "Next" visually shows the previous page
**Location:** `PdfReader.svelte:70-120` (`scrollTop = 0` at line 115).
`pageWindow(center, 1, 2)` yields `[current-1 … current+2]` and canvases are appended in ascending order, so the first canvas is `current-1`. `renderWindow()` unconditionally sets `container.scrollTop = 0`, so after advancing from page 1 to 2 the viewport still shows page 1 while the counter reads 2/N. Scroll the canvas for `current` into view instead of resetting to the top.

### UI-013 — Favorite/watched overrides leak across item navigation
**Location:** `ItemDetail.svelte:17-20`.
`favoriteOverride`/`watchedOverride` are seeded once from the `item` prop and never reset when that prop changes. `/movies/[itemId]` and `/episodes/[itemId]` reuse the component, so after toggling item A, item B displays A's state and toggles B using A's override. Add an effect that re-seeds overrides on `item.id` change.

### UI-014 — Search debounce timer has no cleanup
**Location:** `routes/search/+page.svelte:27-43`.
The effect schedules a 300ms `setTimeout(() => goto(...))` but returns no teardown. If the user clicks a result or nav link before it fires, the timer still navigates back to `/search?q=...`. Return a cleanup that clears `debounce` (the same pattern the logs page uses for its search debounce should be applied there too).

### UI-015 — Songs/genres pagination uses a stale search term after "clear"
**Location:** `routes/music/songs/+page.svelte:15,26-30,63-72`; `routes/music/genres/+page.svelte:15,26-30,44-55`.
`search` is seeded once with `untrack(() => data.query)` and never re-synced. The effect resets `items` and `page` when `data` changes, but `search` still holds the old query. After clicking "clear" (`/music/songs`), the list is unfiltered on page 1, but "Next" calls `loadPage()` with `query: search.trim()`, returning page 2 of the old search mixed into an unfiltered-looking list. Re-sync `search` from `data.query`, or derive the pagination query from `data.query` directly.

### UI-016 — Reader hangs on "Preparing reader…" if token minting fails
**Location:** `routes/books/[bookId]/read/+page.svelte:155-165` (and template at `:480`, `:503`).
`withMediaToken(...).then(...)` has no `.catch()`. If minting rejects (expired session plus failed refresh), `fileUrl` stays `null` and the loading message is permanent. `fileUrl` is also not cleared when `format`/`bookId` change, briefly handing the previous URL to a newly mounted reader. Add a `.catch()` that sets an error state and clear `fileUrl` when inputs change.

### UI-017 — Deleting a library leaves it in the header navigation
**Location:** `routes/admin/libraries/+page.svelte:179-189`.
`createLibrary` (`:143`) and `saveEdit` (`:100`) call `invalidateAll()`, but `removeLibrary` only filters the local array. The root-layout `data.libraries` still contains the deleted library, so the nav dropdown keeps it until some later navigation re-runs the layout load. Add `await invalidateAll()` (with the UI-004 cache fix in place).

### UI-018 — Editing your own account leaves the session stale
**Location:** `routes/admin/users/+page.svelte:74-94`.
`saveEdit` never calls `reloadUser()`. If an admin changes their own role to `user` or ticks "Disabled", `session.user` is unchanged, so the profile page, Admin nav link, and root-layout admin guard keep treating the account as an enabled admin until a full reload. `removeUser` guards against self-disable (`:317`) but the edit form does not. Call `reloadUser()` when editing the current user and add the same self-disable guard.

### UI-019 — Up-next prompt can be shown for the wrong episode
**Location:** `routes/watch/[itemId]/+page.svelte:33-50` and reset effect at `:18-23`.
`handleEnded` awaits `findNextEpisode(data.item.id)` and then assigns `next`. Navigating to another episode while the request is in flight lets the reset effect clear `next`, after which the late response re-sets it, showing the previous episode's prompt. Capture the item id and discard the result if `data.item.id` has changed.

---

## 4. Low severity

**UI-020 — TV UA detection skipped when `localStorage` is undefined** — `lib/tv.svelte.ts:20-21`. `globalThis.localStorage?.getItem(...)` yields `undefined` when the property is absent; `undefined !== null` is `true`, so the function returns `false` and never reaches the UA check, despite the comment promising fall-through. Use `if (stored != null) return stored === '1';`.

**UI-021 — Search input does not follow external `q` changes** — `routes/search/+page.svelte:22-23`. `query`/`type` are initialized once from `data.q`/`data.type`; links into `/search?q=...` or history restore update results but leave the input stale, contradicting the comment about back-button support.

**UI-022 — Next-episode lookup falls back to the first season** — `lib/api/next-episode.ts:43`. If `seasonIndex === -1` (current season missing from the seasons list), the loop starts at `0` and returns the first season as "next". Guard with `if (seasonIndex < 0) return null;`.

**UI-023 — Series "Play" can link to `/watch/<seriesId>`** — `routes/series/[seriesId]/+page.svelte:55-60`. The fallback is used before the async `nextUp`/`episodes` effects resolve and permanently for series with no seasons. `/watch/<seriesId>` is not a playable item — disable the button or link to the series library in that state.

**UI-024 — Stop-session leaves a stale row** — `routes/admin/transcoding/+page.svelte:50-58`. `stopSession` only shows a message; it neither removes the row nor invalidates (and invalidation alone would hit the cached loader at `:13`). Combine with UI-004 and update local state.

**UI-025 — Resolved audio URLs survive logout/token refresh** — `lib/player.svelte.ts:68`, `lib/media-token.svelte.ts:52`. `resolved` caches token-bearing stream URLs indefinitely; `clearMediaToken()` clears only the unscoped token. After logout/login within one page session, queued tracks can still point at the previous user's token. Clear `resolved` (and stop the player) on logout.

**UI-026 — Unhandled rejection on token minting failure** — `Poster.svelte:46-48`, `BookCover.svelte:23-25`. `withMediaToken(...).then(...)` without `.catch()` produces an unhandled rejection and a permanently blank placeholder when token minting fails.

**UI-027 — Gapless preloader never cleaned up** — `AudioBar.svelte:94-108`. A `new Audio()` can be created after the component is destroyed; the old element is paused and has its `src` removed without `load()`. Add teardown that pauses, clears `src`, and calls `load()`.

**UI-028 — Rapid next/previous can desync player state** — `lib/player.svelte.ts:93-106`. `playIndex()` sets `player.track` synchronously but `player.src` only after `await resolveSrc()`. Two quick calls can resolve tokens out of order, showing track B while playing track A. Guard with a generation counter.

**UI-029 — Shelf scroll state goes stale** — `Shelf.svelte:45-60`. The effect reads only `rowEl`; `ResizeObserver` on the row may not fire when `items` changes `scrollWidth`, leaving `hasOverflow`/`canScrollRight` wrong after a data refresh. Depend on `items`.

**UI-030 — Folder-browser race** — `FolderBrowserModal.svelte:23-47`. Overlapping `loadPath()` calls have no generation guard, so an older response can overwrite `currentPath`/`entries`; `showModal()` is also called unconditionally when the effect re-runs.

**UI-031 — Uncleaned timer and image probes** — `PluginConfigModal.svelte:121-123` has a 500ms `setTimeout` that can close a reopened modal; `ComicReader.svelte:99-111` creates `Image` probes whose `onload` can write state after unmount. Add cleanup in both.

---

## 5. Recommended fix order

1. **UI-001, UI-002, UI-003** — each breaks a core media feature outright (PDF reading, 503 resilience, playback on tab switch). Small, localized fixes.
2. **UI-004** — one systemic change (`clearApiCache()` on mutations) resolves or mitigates at least six admin-flow complaints; add `cache: false` to the logs poller.
3. **UI-005–UI-012** — VideoPlayer and PdfReader correctness; these affect the main playback/reading experience.
4. **UI-013–UI-019** — navigation and data-freshness issues in routes.
5. **UI-020–UI-031** — edge cases and resource hygiene; suitable for a follow-up cleanup pass.

## 6. Regression-test recommendations

- Add a mock loader with hls.js's single-use guard to `video-resilience.test.ts` so UI-002 is actually covered.
- Cover the `visibilitychange: visible` case for the VideoPlayer session beacon.
- Assert that a mutation (POST/PUT/DELETE) invalidates a preceding cached GET of the same URL.
- Add a PdfReader render test that verifies `viewport` is passed (or pin a pdf.js type-level check that makes the omission a compile error).

## 7. Areas reviewed with no findings

`capabilities.ts`, `video-subtitles.ts`, `languages.ts`, `blurhash.ts`, all `reader/*` pure helpers, `player/subtitles.svelte.ts`, `player/queue.ts`, `player/seekbar.ts`, `api/next-episode.ts` (aside from UI-022), `api/music-play.ts`, `reader/progress.ts`, `FullPlayer.svelte`, `EpubReader.svelte`, `MediaCard.svelte`, `SubtitleSyncControl.svelte`, `MusicTabs.svelte`, `Pagination.svelte`, `PosterOverlay.svelte`, `admin/missing/*`, root `+layout.*`, and the login/setup/profile/favorites/library/detail routes.
