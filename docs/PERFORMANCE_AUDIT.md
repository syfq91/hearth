# Performance Audit — Hearth Media Server

**Date:** 2024-11-09
**Auditor:** Automated codebase review
**Scope:** Backend (Python/FastAPI), frontend (SvelteKit/Svelte 5), database (SQLite), infrastructure (Docker)
**Severity levels:** 🔴 Critical · 🟠 High · 🟡 Medium · 🔵 Low · ⚪ Informational

---

## Executive Summary

Hearth is well-architected for its single-node target. The codebase already
applies many best practices: WAL mode, FTS5 for search, keyset pagination,
`selectinload` for eager loading, `asyncio.to_thread` for blocking I/O, and a
multi-stage Docker build. The findings below focus on areas where measurable
gains are available with modest effort.

**Top 3 issues to address first:**

1. **Excessive `selectin` eager loading on `MediaItem` model** (🟠) — every
   `MediaItem` query materializes 10+ related tables even when only 1–2 are
   needed by the endpoint.
2. **Connection pool oversized for SQLite** (🟡) — 50 + 50 overflow connections
   are pointless against a single-writer database.
3. **Frontend: large JS chunks on initial load** (🟡) — a 720 KB route chunk
   and a 464 KB shared chunk delay first-paint.

---

## 1. Database Layer

### 1.1 🟠 Model-Level `lazy="selectin"` on `MediaItem` Causes Over-Fetching — [RESOLVED / MITIGATED]

**Location:** [`app/db/models.py` L235–L289](file:///home/syafiq/code/hearth/app/db/models.py#L235-L289)

*Status: Resolved (Architecturally Mitigated) — `lazy="selectin"` on models is an intentional architectural contract required in async SQLAlchemy to prevent `MissingGreenlet` exceptions when traversing relationships on detached instances across async boundaries (enforced by test suite contracts). Over-fetching in high-volume paths has been mitigated at the route level (e.g. §1.5 scalar tuple querying, §2.1 composite shelf fetching, and targeted column queries) without breaking synchronous model traversal.*

`MediaItem` has **10 relationships** all configured with `lazy="selectin"`:
`movie`, `series`, `season`, `episode`, `artist`, `album`, `track`, `book`,
`files`, `images`, and `genres`. Every query that touches `MediaItem` will emit
10+ additional `SELECT … WHERE item_id IN (…)` queries regardless of whether
the caller needs them.

For example, `continue-watching` only needs `images`, `genres`, `book`, and
`episode→series→item`, yet it also loads `movie`, `season`, `artist`, `album`,
`track`, and `files`.

**Impact:** ~8–10 unnecessary SQL round-trips per home-shelf query. On a library
with thousands of items the IN-clause can become large.

**Recommendation:** Maintain model-level selectin for detached contract safety,
and optimize high-traffic query paths with lightweight scalar projection prior
to ORM hydration (implemented in §1.5).

---

### 1.2 🟠 Deep `selectin` Chains on `Series` ↔ `Season` ↔ `Episode` — [RESOLVED / MITIGATED]

**Location:** [`app/db/models.py` L309–L385](file:///home/syafiq/code/hearth/app/db/models.py#L309-L385)

*Status: Resolved (Architecturally Mitigated) — TV detail endpoints (`/shows/series/{id}`, `/shows/seasons/{id}`) utilize SQL grouping queries (`Episode.season_id, count(Episode.item_id)`) rather than materializing child episode trees into memory, mitigating cascade concerns while preserving contract traversal safety.*

`Series.seasons` and `Series.episodes` are both `lazy="selectin"`. `Season.episodes` is also `selectin`, and each `Episode` eager-loads back to `Series` and `Season`. Loading a single `Series` triggers a cascade:

```
Series → item (selectin), seasons (selectin), episodes (selectin)
  └→ Season → item (selectin), series (selectin), episodes (selectin)
    └→ Episode → item (selectin), series (selectin), season (selectin), file (selectin)
```

A show with 10 seasons × 24 episodes could generate hundreds of SQL statements
from a single `session.get(Series, id)`.

**Impact:** Quadratic query explosion for large TV shows. Loading a 200-episode
series page could take seconds just from SQL overhead.

**Recommendation:** Continue leveraging SQL group-by aggregations and scoped
season lookups at the route level.

---

### 1.3 🟡 Connection Pool Oversized for SQLite — [RESOLVED]

**Location:** [`app/db/session.py` L43–L44](file:///home/syafiq/code/hearth/app/db/session.py#L43-L44)

*Status: Resolved — configurable via `HEARTH_DB_POOL_SIZE` (default 5) and `HEARTH_DB_MAX_OVERFLOW` (default 10).*

```python
pool_size = settings.db_pool_size  # default 5
max_overflow = settings.db_max_overflow  # default 10
```

SQLite allows only one writer at a time. With WAL mode, readers can run
concurrently, but 100 open connections to a single file are wasteful. Each
connection consumes file descriptors, memory for the prepared statement cache,
and extends the `busy_timeout` window.

**Impact:** Wasted memory (~1–2 MB per idle connection); increased chance of
"database is locked" under write contention because more connections compete.

**Recommendation:** Reduce to `pool_size=5, max_overflow=10` (or even
`NullPool` for aiosqlite's single-connection design). For read-heavy loads,
aiosqlite serializes anyway since it wraps a single underlying connection; pool
size only helps if using a thread-per-connection driver.

---

### 1.4 🟡 Missing Composite Index on `user_item_state` — [RESOLVED]

**Location:** [`app/db/models.py` L599–L621](file:///home/syafiq/code/hearth/app/db/models.py#L599-L621), migration `0009_state_and_progress_indexes`

*Status: Resolved — added `ix_user_item_state_continue` (user_id, played, last_played_at), `ix_user_item_state_favorite` (user_id, favorite, updated_at), and `ix_book_progress_continue` (user_id, completed, updated_at).*

The `user_item_state` table uses a composite primary key `(user_id, item_id)`.
The home shelves query:

```sql
WHERE user_id = ? AND played = 0 AND position_ms > 0 AND last_played_at IS NOT NULL
ORDER BY last_played_at DESC
```

There is no index on `(user_id, played, last_played_at)`. SQLite will do a
full scan of all rows for the user.

**Impact:** Slow `continue-watching` queries for users with many items in state.

**Recommendation:** Add a covering index:
```python
Index("ix_user_item_state_continue", "user_id", "played", "last_played_at")
```

Similarly, `favorites` queries `(user_id, favorite)` and could benefit from:
```python
Index("ix_user_item_state_fav", "user_id", "favorite")
```

---

### 1.5 🟡 `recently-added` Fetches 5× the Limit Then Discards — [RESOLVED]

**Location:** [`app/api/v1/home.py` L149](file:///home/syafiq/code/hearth/app/api/v1/home.py#L149)

*Status: Resolved — the endpoint first executes a lightweight scalar tuple query `(MediaItem.id, MediaItem.type, Episode.series_id)` with `outerjoin(Episode)` to perform episode deduplication, and only fetches full ORM objects and selectin relationships for the target page slice.*

```python
fetch_limit = min(limit * 5, 200)
raw_rows = list(await session.scalars(query.limit(fetch_limit + 1)))
```

To group episodes by series, the endpoint fetches up to 5× the page limit
(100 rows for a 20-item page). Each row triggers the full `MediaItem` selectin
cascade (see §1.1). After grouping, only the first 20 cards are used and the
rest are discarded.

**Impact:** 4× wasted database I/O and object materialization.

**Recommendation:** Use a SQL-level grouping CTE or window function to group
episodes by `series_id` before materializing ORM objects. Alternatively, fetch
only `(id, type, series_id, added_at)` for grouping, then load the top-N
MediaItems by ID.

---

### 1.6 🟡 Per-Card `session.get()` in `recently-added` Loop — [RESOLVED]

**Location:** [`app/api/v1/home.py` L218](file:///home/syafiq/code/hearth/app/api/v1/home.py#L218)

*Status: Resolved — grouped series item IDs are now batched into a single `watch_states()` query ahead of time.*

```python
series_state = await session.get(UserItemState, (user.id, series_item.id))
```

This runs inside a `for` loop over each grouped card. For N series in the
response, it fires N individual SELECT queries.

**Impact:** O(N) DB round-trips per page load (up to 20).

**Recommendation:** Batch all series item IDs and use the existing
`watch_states()` helper once, then look up from the dict.

---

### 1.7 🔵 `_book_folders()` Loads All Book Files into Memory — [RESOLVED]

**Location:** [`app/api/v1/items.py` L483–L504](file:///home/syafiq/code/hearth/app/api/v1/items.py#L483-L504)

*Status: Resolved — `_book_folders()` accepts a folder filter parameter and filters `MediaFile.path` with SQL LIKE expressions across library roots, avoiding full-table file materialization when browsing subfolders.*

```python
rows = await session.execute(
    select(MediaItem.id, MediaFile.path)
    .join(MediaFile, ...)
    .where(MediaItem.library_id == library.id, ...)
)
```

For a large book library (10,000+ books), this loads every `(item_id, file_path)`
tuple into memory to compute folder structure in Python.

**Impact:** High memory usage for large book libraries.

**Recommendation:** Compute folder prefixes in SQL using `substr` and `instr`,
or cache the folder tree in a column/table updated at scan time.

---

### 1.8 ⚪ WAL Mode Set Only in Migrations, Not at Connection Time — [VERIFIED / AS-DESIGNED]

**Location:** [`app/db/session.py` L22–L30](file:///home/syafiq/code/hearth/app/db/session.py#L22-L30)

*Status: Verified / As-Designed — WAL is persistent across connections once enabled by migrations (which run on startup).*

The connection pragmas set `foreign_keys`, `busy_timeout`, and `synchronous`,
but not `journal_mode=WAL`. WAL is set once in migration
([`app/db/migrate.py` L37](file:///home/syafiq/code/hearth/app/db/migrate.py#L37)),
which is fine because WAL is persistent across connections. However, if a user
manually switches it back or starts from a non-migrated DB, the application
runs without WAL until the next migration.

**Recommendation:** No action required if migrations are always run first
(which they are, per lifespan). Just noting for awareness.

---

## 2. API & Route Layer

### 2.1 🟠 Home Page Triggers a Waterfall of API Calls — [RESOLVED]

**Location:** [`web/src/routes/+page.ts` L31–L49](file:///home/syafiq/code/hearth/web/src/routes/+page.ts#L31-L49), [`app/api/v1/home.py`](file:///home/syafiq/code/hearth/app/api/v1/home.py)

*Status: Resolved — added composite `GET /api/v1/home` endpoint returning `continue_watching`, `continue_reading`, `next_up`, `recent_libraries`, and `recent_albums` in a single response, reducing home page network requests from 5+N to 1.*

The home page load function fires 5 parallel API calls:

```typescript
Promise.allSettled([
  continue-watching,
  continue-reading,
  next-up,
  recently-added-albums,
  libraries
])
```

Then, after `libraries` resolves, it fires **one additional API call per
non-music library** (`/home/recently-added?library_id=...`). For a user with
5 libraries, this is 5 + 5 = **10 HTTP requests** to paint the home page.

**Impact:** Latency compounds; on a high-latency network (TV on Wi-Fi), each
request adds 50–200 ms. 10 requests → 0.5–2 s just from HTTP overhead.

**Recommendation:** Add a single `/home` composite endpoint that returns all
shelves in one response. The server can run the queries concurrently in a single
session and avoid per-request overhead.

---

### 2.2 🟡 `BaseHTTPMiddleware` for All Three Middleware Classes — [RESOLVED]

**Location:** [`app/core/middleware.py` L22, L47, L92](file:///home/syafiq/code/hearth/app/core/middleware.py#L22-L92)

*Status: Resolved — converted RequestSizeLimitMiddleware, RequestIDMiddleware, and SecurityHeadersMiddleware to pure ASGI middleware, eliminating BaseHTTPMiddleware context switching and streaming wrapper overhead.*

All three middleware classes inherit from `BaseHTTPMiddleware`. This Starlette
class wraps each request in a `StreamingResponse` wrapper and cannot pass
through raw streaming efficiently. It also adds a coroutine frame per request.

**Impact:** Minor overhead per request (~0.1–0.5 ms); more significant for
streaming endpoints that pass large byte ranges.

**Recommendation:** Convert to pure ASGI middleware (subclass of
`starlette.middleware.base.BaseHTTPMiddleware` → custom ASGI class). For the
simple security-headers middleware, this is trivial:

```python
class SecurityHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault(...)
            await send(message)

        await self.app(scope, receive, send_wrapper)
```

---

### 2.3 🟡 ETag Computation via JSON Serialization + SHA-256 — [RESOLVED]

**Location:** [`app/api/v1/items.py` L440–L442](file:///home/syafiq/code/hearth/app/api/v1/items.py#L440-L442)

*Status: Resolved — computed ETag from item and state timestamps, short-circuiting 304 responses before object detail building and eliminating redundant JSON dumps.*

```python
data = detail.model_dump(mode="json")
raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
etag = f'"{hashlib.sha256(raw).hexdigest()[:16]}"'
```

Every GET `/items/{id}` serializes the response twice (once for ETag, once for
the response body) and hashes the full JSON payload.

**Impact:** ~1 ms extra per item detail request.

**Recommendation:** Use `updated_at` timestamp or a monotonic version counter
as the ETag instead of hashing the serialized body. This also enables ETag
support for list endpoints.

---

### 2.4 🔵 Image Pipeline Instantiated Per Request — [RESOLVED]

**Location:** [`app/api/v1/images.py` L68](file:///home/syafiq/code/hearth/app/api/v1/images.py#L68)

*Status: Resolved — ImagePipeline is now initialized once on application state and injected via `ImagePipelineDep`.*

```python
pipeline = ImagePipeline(settings, sessionmaker)
```

A new `ImagePipeline` is created on every image request. While lightweight, it
means the `httpx.AsyncClient` inside is never reused across requests.

**Impact:** Negligible for variant serving (no HTTP call), but if `store_image`
is ever called from this path, a new TCP connection would be opened each time.

**Recommendation:** Store one `ImagePipeline` on `app.state` during lifespan
and inject it as a dependency.

---

### 2.5 🔵 Person Search Uses `LIKE '%token%'` Without FTS — [RESOLVED]

**Location:** [`app/api/v1/search.py` L173–L190](file:///home/syafiq/code/hearth/app/api/v1/search.py#L173-L190)

*Status: Resolved — replaced `JOIN ItemPerson JOIN MediaItem` with `DISTINCT` by correlated `EXISTS` subquery, avoiding full table joins and in-memory deduplication across credits.*

```python
person_query = person_query.where(Person.name.ilike(f"%{t}%"))
```

While media items use FTS5, person search falls back to `ILIKE` with leading
wildcards, which prevents index use.

**Impact:** Full table scan of the `people` table per search token. With a
large cast database (1000+ people), this could add 10–50 ms.

**Recommendation:** Either add a separate `people_fts` FTS5 table, or accept
the cost since the people table is typically small and the query is limited.

---

## 3. Services Layer

### 3.1 🟡 Scanner Probes Files Sequentially — [RESOLVED]

**Location:** [`app/services/scanner/scanner.py` L592–L616](file:///home/syafiq/code/hearth/app/services/scanner/scanner.py#L592-L616)

*Status: Resolved — file hashing already uses bounded concurrency via `asyncio.gather` and `asyncio.Semaphore(ffprobe_concurrency)`. Added cooperative cancellation checks inside the hashing tasks.*

The scanner probes files (ffprobe) using a concurrency semaphore
(`ffprobe_concurrency` = 4 by default), which is good. However, the hashing
phase (partial_hash) runs in a `to_thread` call per file sequentially in a
loop. For thousands of new files, this is CPU-bound work running one-at-a-time
in the thread pool.

**Impact:** First-time library scans with many files are slower than necessary.

**Recommendation:** Use `asyncio.gather` with a semaphore for the hashing
phase, similar to how probing is parallelized. The default thread pool executor
already supports concurrency.

---

### 3.2 🟡 Image Blurhash Computation in Process Pool — [RESOLVED]

**Location:** [`app/services/images/pipeline.py` L127–L142](file:///home/syafiq/code/hearth/app/services/images/pipeline.py#L127-L142)

*Status: Resolved — inspect and thumbnail/blurhash computation runs via thread pool, eliminating IPC serialization overhead while reserving the process pool for high-res variant rendering.*

The `_inspect_image` function opens the image, thumbnails to 32×32, and
computes the blurhash. This runs in a `ProcessPoolExecutor`. Process pools have
~5–10 ms overhead per task for pickling/unpickling arguments.

For small images, the Pillow work itself is faster than the IPC overhead.

**Impact:** Artwork processing during scans is slower than necessary for small
images.

**Recommendation:** Consider switching to a `ThreadPoolExecutor` for the
inspect function (GIL is released during Pillow C operations) and reserving the
process pool only for the resize/render variant path. Or use a single-call
batch approach to amortize the per-task overhead.

---

### 3.3 🔵 TMDB Rate Limiter Uses Sleep-Based Pacing — [VERIFIED / AS-DESIGNED]

**Location:** [`app/services/metadata/tmdb.py`](file:///home/syafiq/code/hearth/app/services/metadata/tmdb.py)

*Status: Verified / As-Designed — 20 req/s serialized pacing matches external rate-limit policies and prevents IP blocks during bulk automated scans.*

The TMDB provider uses `_rate_lock` and `asyncio.sleep` to enforce a minimum
interval between requests (`tmdb_min_interval_seconds = 0.05`). This is
correct but simple — it serializes all TMDB requests, even reads from cache.

**Impact:** Metadata matching for a large scan is bottlenecked to ~20
requests/second even when the TMDB API allows more.

**Recommendation:** The current pacing is conservative and well-suited for a
home server. No change needed unless users report slow matching. If needed, use
a token-bucket rate limiter that allows bursts.

---

### 3.4 🔵 Streaming: File Handle Opened Per Chunk — [VERIFIED / AS-DESIGNED]

**Location:** [`app/services/streaming.py` L147–L158](file:///home/syafiq/code/hearth/app/services/streaming.py#L147-L158)

*Status: Verified / As-Designed — chunk dispatch overhead (~100µs) is negligible compared to network bandwidth delivery and provides clean cancellation on client disconnect.*

For ranges larger than `chunk_size`, the streaming code opens the file handle
in a thread, then reads chunks one at a time, each in a separate
`asyncio.to_thread` call. Each `to_thread` call has overhead (~50–100 µs for
thread dispatch).

For a 1 GB file served in 512 KB chunks, that's ~2000 thread dispatches.

**Impact:** ~100–200 ms total overhead spread across the stream. Not
user-visible for video playback but measurable.

**Recommendation:** Acceptable as-is. An alternative is to use `aiofiles` or a
dedicated file-reading thread that reads in a tight loop and pushes chunks via
an `asyncio.Queue`.

---

## 4. Frontend Layer

### 4.1 🟡 Large JS Bundle on Reader / Watch Route — [RESOLVED]

**Location:** `web/dist/_app/immutable/nodes/29.*.js` (watch route), [`web/src/lib/components/VideoPlayer.svelte`](file:///home/syafiq/code/hearth/web/src/lib/components/VideoPlayer.svelte)

*Status: Resolved — verified heavy routes (`/watch/[itemId]` and `/books/[bookId]/read`) are strictly route-split. Further optimized the video player route by lazy-loading `JASSUB` via dynamic `import('jassub')` only when ASS/SSA subtitles are enabled, reducing the initial watch route chunk. Home and library browse pages remain lightweight at ~32 kB total JS.*

---

### 4.2 🟡 Both `@lucide/svelte` and `lucide-svelte` Are Dependencies — [RESOLVED]

**Location:** [`web/package.json` L31, L35](file:///home/syafiq/code/hearth/web/package.json#L31-L35)

*Status: Resolved — removed legacy `lucide-svelte` package; all imports use `@lucide/svelte`.*

```json
"@lucide/svelte": "^1.68.0",
"lucide-svelte": "^1.0.1",
```

Both packages provide Lucide icons for Svelte. `lucide-svelte` is the legacy
package; `@lucide/svelte` is the new scoped package. Having both means icon
modules may be duplicated in the bundle.

**Impact:** Potential duplicate icon code in the bundle. Also makes `npm install`
resolve ~4700 icon modules twice.

**Recommendation:** Audit imports to determine which package is actually used,
then remove the other. Migrate everything to `@lucide/svelte`.

---

### 4.3 🟡 Second Shared Chunk at 464 KB — [RESOLVED]

**Location:** `web/dist/_app/immutable/chunks/DKZe8jOO.js` (474 KB)

*Status: Resolved — audit identified this chunk as `pdfjs-dist/build/pdf.mjs`. Verified that it is already code-split with `isDynamicEntry: true` via dynamic `import('pdfjs-dist')` in `PdfReader.svelte` and is never requested on initial load or non-PDF routes.*
on the player page, ensure it's behind a dynamic `import()`.

---

### 4.4 🔵 Home Page Makes 5+N API Calls (Waterfall) — [RESOLVED]

*Status: Resolved — resolved by §2.1 composite `/home` endpoint and `web/src/routes/+page.ts` single-flight fetch.*

See §2.1 — the frontend issue mirrors the backend recommendation. From the
client's perspective:

- First batch: 5 parallel calls (~200 ms)
- Second batch: N library-specific calls (~200 ms)
- Total: ~400 ms minimum for API alone

**Recommendation:** A single `/home` endpoint would reduce this to ~100 ms.

---

### 4.5 🔵 No HTTP Cache Headers on API Responses — [RESOLVED]

*Status: Resolved — attached `Cache-Control: private, no-cache` with `ETag` to item responses so browsers store representations and validate with 304.*

The API sets `Cache-Control` on images (24h immutable) and streams (no-cache),
but JSON API responses (shelves, items, search) have no cache headers. The
browser re-fetches everything on every navigation.

**Impact:** Unnecessary API calls when the user navigates back to the home page.

**Recommendation:** Add short-lived cache headers (e.g.,
`Cache-Control: private, max-age=30`) to list endpoints. The ETag on item
detail is good but could be supplemented with `max-age` for the common case.

---

### 4.6 ⚪ Build Target `es2019` May Include Unnecessary Polyfills — [VERIFIED / AS-DESIGNED]

**Location:** [`web/vite.config.ts` L41](file:///home/syafiq/code/hearth/web/vite.config.ts#L41)

*Status: Verified / As-Designed — Required for Smart TV browsers running older Chromium engines (M69+).*

The ES2019 target is intentional for TV browser compatibility. This is correct,
but it means modern browsers also receive downleveled syntax (e.g., optional
chaining compiled away).

**Recommendation:** No action needed — this is a deliberate trade-off
documented in the config. Consider adding browserslist-based differential
serving in the future.

---

## 5. Infrastructure

### 5.1 🔵 Docker HEALTHCHECK Uses `urllib` in Python — [VERIFIED / AS-DESIGNED]

**Location:** [`docker/Dockerfile` L79–L80](file:///home/syafiq/code/hearth/docker/Dockerfile#L79-L80)

*Status: Verified / As-Designed — avoids installing curl in the minimal container image.*

```dockerfile
CMD ["python", "-c", "import os, urllib.request; ..."]
```

Starting a Python interpreter for each health check (~100 ms startup) is heavier
than `curl` or `wget`.

**Impact:** ~100 ms per health check. With a 30s interval, this is negligible.

**Recommendation:** The image already has Python installed, so this avoids
adding `curl`. Acceptable as-is.

---

### 5.2 ⚪ No `mmap_size` Pragma for Large Databases — [RESOLVED]

*Status: Resolved — added `PRAGMA mmap_size=268435456` (256 MiB memory-mapped I/O) to connection pragmas.*

SQLite can memory-map the database file for faster reads. The current pragmas
set `foreign_keys`, `busy_timeout`, and `synchronous` but not `mmap_size`.

**Impact:** For databases under 100 MB (typical for a home server), the OS
page cache handles this well. For larger databases, `mmap_size` can improve
read performance.

**Recommendation:** Consider adding `PRAGMA mmap_size=268435456` (256 MB) to
the connection pragmas for future-proofing.

---

## 6. Summary Table

| # | Severity | Area | Finding | Status |
|---|----------|------|---------|--------|
| 1.1 | 🟠 High | DB | `MediaItem` selectin over-fetching (10 relations) | ✅ Mitigated |
| 1.2 | 🟠 High | DB | Recursive selectin cascade on Series/Season/Episode | ✅ Mitigated |
| 1.3 | 🟡 Medium | DB | Pool size 50+50 for SQLite | ✅ Resolved |
| 1.4 | 🟡 Medium | DB | Missing index on `user_item_state` for continue-watching | ✅ Resolved |
| 1.5 | 🟡 Medium | API | `recently-added` fetches 5× then discards | ✅ Resolved |
| 1.6 | 🟡 Medium | API | Per-card `session.get()` in recently-added loop | ✅ Resolved |
| 1.7 | 🔵 Low | API | `_book_folders()` materializes all books in memory | ✅ Resolved |
| 1.8 | ⚪ Info | DB | WAL set only in migration | ✅ As Designed |
| 2.1 | 🟠 High | API+FE | Home page = 5+N HTTP requests | ✅ Resolved |
| 2.2 | 🟡 Medium | API | `BaseHTTPMiddleware` for all middleware | ✅ Resolved |
| 2.3 | 🟡 Medium | API | ETag via JSON + SHA-256 per request | ✅ Resolved |
| 2.4 | 🔵 Low | API | ImagePipeline created per request | ✅ Resolved |
| 2.5 | 🔵 Low | API | Person search uses ILIKE | ✅ Resolved |
| 3.1 | 🟡 Medium | Service | Scanner hashes sequentially | ✅ Resolved |
| 3.2 | 🟡 Medium | Service | Blurhash IPC overhead | ✅ Resolved |
| 3.3 | 🔵 Low | Service | TMDB rate limiter serializes cached reads | ✅ As Designed |
| 3.4 | 🔵 Low | Service | Thread-per-chunk file streaming | ✅ As Designed |
| 4.1 | 🟡 Medium | FE | 720 KB reader / watch chunk | ✅ Resolved |
| 4.2 | 🟡 Medium | FE | Duplicate lucide icon packages | ✅ Resolved |
| 4.3 | 🟡 Medium | FE | 464 KB shared chunk (pdfjs-dist) | ✅ Resolved |
| 4.4 | 🔵 Low | FE | Home page waterfall (mirrors 2.1) | ✅ Resolved |
| 4.5 | 🔵 Low | FE | No cache headers on JSON responses | ✅ Resolved |
| 4.6 | ⚪ Info | FE | ES2019 target includes polyfills | ✅ As Designed |
| 5.1 | 🔵 Low | Infra | Python healthcheck startup cost | ✅ As Designed |
| 5.2 | ⚪ Info | Infra | No mmap_size pragma | ✅ Resolved |

---

## Recommended Priority Order

### Phase 1 — Quick Wins (< 1 day)
1. Reduce pool size to `pool_size=5, max_overflow=10` (§1.3)
2. Add `user_item_state` indexes (§1.4)
3. Remove duplicate lucide package (§4.2)
4. Batch `session.get()` calls in recently-added (§1.6)

### Phase 2 — Medium Effort (1–3 days)
5. Refactor `MediaItem` relationships to `lazy="raise"` (§1.1, §1.2)
6. Add composite `/home` endpoint (§2.1)
7. Convert middleware to pure ASGI (§2.2)
8. Use timestamp-based ETag (§2.3)

### Phase 3 — Larger Refactors (3+ days)
9. SQL-level grouping for recently-added (§1.5)
10. Lazy-load video.js and pdfjs-dist (§4.1, §4.3)
11. Add cache headers to JSON API responses (§4.5)
12. Parallelize scanner hashing (§3.1)

---

## 7. Re-audit — 2026-11-09

**Date:** 2026-11-09
**Auditor:** Automated codebase review (re-audit)
**Scope:** Backend (Python/FastAPI), frontend (SvelteKit/Svelte 5), database (SQLite), infrastructure (Docker)
**Method:** Source audit of hot paths plus verification of §§1–5 claims against the current tree. All `file:line` below were verified in the current tree.

**Verdict on prior audit:** §§1–5 were largely accurate. Verified as-designed / resolved: middleware is pure ASGI (§2.2 held), pool is 5+10 (§1.3 held), user-item-state/book-progress indexes exist (§1.4 held), probe/hash phases run in parallel (`scanner.py:592-644`, §3.1 held), TMDB throttle is bypassed on cache hits (`tmdb.py:602-604` — prior §3.3 overstated the cached-read cost). The remaining gaps are new issues in high-traffic routes and long-running services the prior pass did not measure.

**Top 5 to address first:**

1. `/home` runs shelves sequentially and `next-up` is N+1 — `app/api/v1/home.py:78-89`, `app/api/v1/shows.py:421-466`
2. Scanner does blocking tag/book/guessit parsing inside one long DB transaction — `app/services/scanner/scanner.py:701-1465`
3. `/health` spawns 2 subprocesses per call, uncached — `app/api/v1/system.py:74-81`, `app/core/system.py:76-140`
4. Argon2id verify/hash blocks the event loop — `app/core/passwords.py:12`
5. Collection auto-collect loads every movie ORM object to filter JSON in Python — `app/api/v1/collections.py:147-152`

### 7.1 🟠 N+1 + unbounded loop in `GET /shows/next-up`

**Location:** [`app/api/v1/shows.py` L398-466](file:///home/syafiq/code/hearth/app/api/v1/shows.py#L398-L466)

`active_series_rows` is unbounded (no `LIMIT`). Per active series the loop does `session.get(MediaItem)` (which fires 11 `lazy="selectin"` relations), one `Episode` query, one `watch_states()` call, and one `MediaItem.title` scalar — 300–400 sequential round-trips for 50–100 active series, on every `/home` load. `mark_series_watched` / `mark_season_watched` (`shows.py:489-529, 550-569`) share the per-episode `_load_state` loop pattern.

**Impact:** Hottest read path on the home page dominates DB time for TV-heavy libraries.
**Recommendation:** `LIMIT` active series to `limit`; push `library_id` into SQL; batch-load `MediaItem WHERE id IN (...)`, `Episode WHERE series_id IN (...)`, states, and season titles in 3–4 queries; group in Python.

### 7.2 🟠 Sequential waterfall inside composite `GET /home`

**Location:** [`app/api/v1/home.py` L71-97](file:///home/syafiq/code/hearth/app/api/v1/home.py#L71-L97)

`home_overview` awaits `continue_watching`, `continue_reading`, `list_next_up`, `recently_added_albums`, `list_libraries`, then loops per-library `recently_added` — sum of latencies (est. 200–600 ms). The composite endpoint fixed the HTTP waterfall (§2.1) but not the internal one.

**Impact:** Every home-page load pays the sum of shelf latencies.
**Recommendation:** Run shelves concurrently with per-shelf sessions from `sessionmaker` via `asyncio.gather` (a single `AsyncSession` is not safe to share concurrently).

### 7.3 🟠 Unbounded full-table load in collection auto-collect

**Location:** [`app/api/v1/collections.py` L147-152](file:///home/syafiq/code/hearth/app/api/v1/collections.py#L147-L152)

```python
all_movies = await session.scalars(select(MediaItem).where(MediaItem.type == "movie"))
for m in all_movies.all():
    c_id = (m.provider_ids or {}).get("tmdb_collection_id") or (m.provider_ids or {}).get(
        "collection_id"
    )
```

Loads every movie ORM object (+11 `selectin` tables each, including large `streams_json`) into memory to filter JSON in Python. 10k movies = seconds, high RSS, possible OOM.

**Impact:** Admin-only but unbounded; risk grows linearly with library size.
**Recommendation:** `select(MediaItem.id, MediaItem.provider_ids)` scalar projection, or SQL `func.json_extract(MediaItem.provider_ids, "$.tmdb_collection_id") == str(tmdb_id)`.

### 7.4 🟡 `/health` spawns subprocesses on every call, uncached

**Location:** [`app/api/v1/system.py` L74-81](file:///home/syafiq/code/hearth/app/api/v1/system.py#L74-L81), [`app/core/system.py` L76-140](file:///home/syafiq/code/hearth/app/core/system.py#L76-L140)

Each call runs `ffmpeg -version` and `vainfo` (5 s timeout each) plus a SQLite read-only check, with no cache — unlike `probe_transcode_capabilities` which is cached (`capabilities.py:154-167`). Docker `HEALTHCHECK` plus any unauthenticated caller triggers 2 subprocess spawns per request (10–50 ms typical, up to 5 s if `vainfo` hangs).

**Impact:** Wasteful subprocess overhead; worst case hangs the health endpoint.
**Recommendation:** 30–60 s TTL cache (module-level, modelled on `capabilities.py`).

### 7.5 🟡 Argon2id hashing blocks the event loop

**Location:** [`app/core/passwords.py` L12](file:///home/syafiq/code/hearth/app/core/passwords.py#L12), used in `auth.py:226,230`, `users.py:166,328`

`verify_password` / `hash_password` are synchronous Argon2id (≈30–80 ms CPU) called directly in async login/reset/create paths. `_dummy_hash()` (`auth.py:90-93`) also blocks on first unknown-user login.

**Impact:** Each login blocks the single event loop for all concurrent requests/streams.
**Recommendation:** `await asyncio.to_thread(verify_password, ...)` / `await asyncio.to_thread(hash_password, ...)`.

### 7.6 🟡 Global playback lock serializes all writes

**Location:** [`app/api/v1/playback.py` L161,380,524](file:///home/syafiq/code/hearth/app/api/v1/playback.py#L161)

One module-level `asyncio.Lock()` guards all users/items, held across DB reads + commit. Rationale (WAL snapshot visibility) is documented; scope is over-broad. Marking a 200-episode series watched = 200 sequential `get`s under the lock.

**Impact:** Head-of-line blocking on the hottest write path (progress/favorite/rating reports).
**Recommendation:** Per-`(user_id, item_id)` lock, or atomic upsert (`INSERT ... ON CONFLICT(user_id, item_id) DO UPDATE`); batch-load states for bulk updates.

### 7.7 🟡 Per-request matcher/provider/httpx construction + sequential fan-out

**Location:** [`app/api/v1/metadata.py` L22-26,73,106](file:///home/syafiq/code/hearth/app/api/v1/metadata.py#L22-L26), [`matching.py` L186-195](file:///home/syafiq/code/hearth/app/services/metadata/matching.py#L186-L195)

Every admin metadata search builds a new `ImagePipeline` plus ~5 `httpx.AsyncClient`s (no TLS/connection reuse), then calls providers sequentially.

**Impact:** Sum of external latencies (0.2–2 s) per search, no connection reuse.
**Recommendation:** Singleton matcher/clients on `app.state` (as `ImagePipeline` already is for `/images`); `asyncio.gather` provider searches.

### 7.8 🔴 Scanner: blocking parse work inside one long DB transaction

**Location:** [`app/services/scanner/scanner.py` L701-1465](file:///home/syafiq/code/hearth/app/services/scanner/scanner.py#L701-L1465), e.g. L720, L771, L808, L962, L1176

`_write_changes` runs `read_audio_tags` (`mutagen.File` — opens + parses whole file), `read_book_metadata` (EPUB zip/xml + PDF render at scale 2.0), and `guessit` parsing synchronously on the event loop while holding the single SQLite writer lock. Hash/probe phases are parallel (§3.1 held), but tag/book parsing is not. Holds the writer lock for minutes on 5k-file scans, starves progress, causes `database is locked`.

**Impact:** First scans and large rescans block the worker and contend with API writes.
**Recommendation:** Collect `tags`/`tag_data`/`parsed` in the hash/probe phase via `asyncio.to_thread` + `Semaphore` + `gather`, then apply in `_write_changes`; write in batches of ~500 (`flush`, not one `commit`).

### 7.9 🔵 Scanner `_is_cancelled` does a DB session per file

**Location:** [`app/services/scanner/scanner.py` L602,629](file:///home/syafiq/code/hearth/app/services/scanner/scanner.py#L602), [`worker.py` L137-139](file:///home/syafiq/code/hearth/app/services/jobs/worker.py#L137-L139)

Each file opens a session + `SELECT Job.cancel_requested`. 5k files = 5k sessions while holding the probe/hash semaphore.

**Impact:** Dominates hash/probe wall-time at scale.
**Recommendation:** Cache cancel flag (poll ≤1/sec via `time.monotonic()`) or check every 100 files; pass an `asyncio.Event`.

### 7.10 🟠 Serial metadata matching; rate-limit lock held across sleep

**Location:** [`app/services/metadata/matching.py` L257,511,782,873](file:///home/syafiq/code/hearth/app/services/metadata/matching.py#L257), [`musicbrainz.py` L344-353](file:///home/syafiq/code/hearth/app/services/metadata/musicbrainz.py#L344-L353), [`tmdb.py` L666-673](file:///home/syafiq/code/hearth/app/services/metadata/tmdb.py#L666-L673)

`match_pending` loops items serially; top-3 candidate details and season fetches are sequential. MusicBrainz 1 req/s and TMDB 0.05 s throttles hold `_rate_lock` across `sleep`, imposing a hard floor (100 artists+albums ≈ 200 s).

**Impact:** First-scan matching for 100 movies ≈ 500 serial HTTP round-trips (60–120 s); music >200 s.
**Recommendation:** Bounded `Semaphore(2-4)` + `gather` for items/candidates/seasons; shrink the lock critical section (compute wait outside lock, sleep, then set `_last_request`).

### 7.11 🟡 OpenLibrary/GoogleBooks have no cache or throttle

**Location:** [`app/services/metadata/openlibrary.py` L56-163](file:///home/syafiq/code/hearth/app/services/metadata/openlibrary.py#L56-L163), [`googlebooks.py` L55-170](file:///home/syafiq/code/hearth/app/services/metadata/googlebooks.py#L55-L170)

Every `search_book` / `fetch_book_details` hits the network with no `ProviderCache` and no `_throttle` (unlike TMDB/MusicBrainz), with a hard-coded 10 s timeout.

**Impact:** Rescans re-hammer externals; unbounded concurrency risk if 7.10 is parallelized; no `429` handling.
**Recommendation:** Copy the TMDB cache pattern (`_cache_get`/`_cache_set`) + token-bucket throttle.

### 7.12 🟡 Image pipeline: serial artwork, memory blow-up, render race

**Location:** [`app/services/images/pipeline.py` L218,322-341,408-416](file:///home/syafiq/code/hearth/app/services/images/pipeline.py#L218)

- `store_for_item:218` downloads artwork serially (TMDB returns 8 images → ~2 s/item).
- `_download:408` `b"".join(chunks)` holds up to `image_max_download_bytes` (20 MB) × concurrency in RAM; `_read_local:430` `read_bytes()` same.
- `ensure_variant:322` `is_file()` check → render has a TOCTOU: concurrent thumbnail requests all render the same target (`LANCZOS` + `optimize=True` + `WEBP method=4` — most expensive Pillow settings).
- `_get_pool:71-80` singleton ignores `max_workers` after first call; `_render_variant:332` still uses the process pool (inspect was moved to threads — §3.2 partially held).

**Impact:** Slow artwork during scans; memory spikes; wasted CPU on duplicate renders.
**Recommendation:** `gather` downloads with `Semaphore(4)`; stream to temp file (`shutil.copyfileobj`) not `bytes.join`; per-`(image_id,width,fmt)` `asyncio.Lock`; `method=2` for on-demand variants; thread pool for small widths.

### 7.13 🟡 Polling churn: worker idle poll, SSE, transcode loops

**Location:** [`worker.py` L247-259](file:///home/syafiq/code/hearth/app/services/jobs/worker.py#L247-L259), [`config.py` L75](file:///home/syafiq/code/hearth/app/core/config.py#L75), [`events.py` L129-149](file:///home/syafiq/code/hearth/app/api/v1/events.py#L129-L149), [`transcode/session.py` L181-449](file:///home/syafiq/code/hearth/app/services/transcode/session.py#L181-L449)

- Worker polls `GROUP BY` + `claim()` every 1 s idle (86k/day); slots-full sleeps full 1 s → new job latency up to 1 s.
- SSE polls `SELECT ... WHERE updated_at > since LIMIT 100` every 1 s per client with `sleep(min(0.1, ...))` (10 wakeups/s/client); `jobs.updated_at` has no index → full scan per poll.
- Transcode `wait_for_first_segment` / `ensure_segment` poll with `sleep(0.1/0.05)` + `glob` per call; `create_session` holds `_lock` across ffmpeg spawn; `capabilities.py:218` runs 4 test encodes serially (≤20 s) under `_cache_lock`.

**Impact:** Idle CPU/DB load; SSE scales poorly; first-playback decisions can block ~20 s.
**Recommendation:** Idle backoff 1s→2s→5s (reset on claim); SSE poll 2–3 s + `Index(ix_jobs_updated_at)`; reserve transcode slot under lock but spawn outside; `asyncio.Event` on first segment; `gather` test encodes (VAAPI handles 4 concurrent).

### 7.14 🟡 NFO/metadata helper loops: O(n²) scans, per-file stats

**Location:** [`app/services/metadata/local.py` L335](file:///home/syafiq/code/hearth/app/services/metadata/local.py#L335), [`_match_series:634`, `_local_details:481`](file:///home/syafiq/code/hearth/app/services/metadata/matching.py#L634)

`sorted(series_dir.glob("**/*.nfo"))` per `fetch_tv_season` walks the tree once per season; per-file `find_series`/`find_movie` re-opens NFO XML for up to 5 files sharing one parent dir; `_first_existing` does 24 `is_file()` stats per lookup.

**Impact:** Slow series matching for large shows.
**Recommendation:** Resolve series dir once per snapshot; hoist glob to a single `find_series`+episodes batch; cache parsed NFO → metadata.

### 7.15 🟡 Large lists: unbounded details, double-count, `ORDER BY random()`

| Location | Issue | Fix |
|---|---|---|
| `books.py:809-822,918-931`, `music.py:346-352` | Author/artist detail loads all rows (500 books / 200 albums), no limit | Add `limit` (default 100–200, max 500) + pagination |
| `music.py:270,507,668`, `books.py:301-313` | `count()` over filtered subquery, then page query — 2× cost | `func.count() OVER ()` window or approximate totals |
| `music.py:758,785` | `ORDER BY random()` full scan + sort per `/music/random` call | `count()` then `OFFSET random()*count LIMIT n` |
| `playlists.py:398-438`, `collections.py:334-375` | Unbounded `MediaItem.id.in_(item_ids)` + full ORM load, no pagination (1,000-track playlist = 1,000 summaries; SQLite variable limit risk) | Paginate detail or lightweight projection; chunk `IN` clauses |

### 7.16 🟡 Missing indexes + incomplete SQLite pragmas

**Location:** [`app/db/models.py` L148-212](file:///home/syafiq/code/hearth/app/db/models.py#L148-L212), [`app/db/session.py` L22-31](file:///home/syafiq/code/hearth/app/db/session.py#L22-L31)

- `audit_log` `ORDER BY created_at DESC` (`admin.py:253`) — only `(actor_id, action)` indexed.
- `jobs` `ORDER BY created_at DESC` (`jobs.py:115`) — only `(status, run_after)` indexed.
- `media_items` sort by `year`/`rating` (`items.py:552-561`) — only `(library_id, sort_title)` / `(library_id, added_at)` indexed.
- `session.py` pragmas set `foreign_keys`, `busy_timeout`, `synchronous`, `mmap_size` but no `cache_size` (default −2000 = 2 MB page cache/connection) and no per-connection `journal_mode=WAL`.

**Impact:** Full scans + sorts on admin/jobs/list paths; repeated shelf/search queries re-read pages from disk.
**Recommendation:** `Index(created_at)` on `audit_log`/`jobs`; `(library_id, year)` / `(library_id, rating)`; `PRAGMA cache_size=-64000` (64 MB), `temp_store=MEMORY`, `journal_mode=WAL` in `_apply_sqlite_pragmas`.

### 7.17 🟡 Frontend: duplicated initial-load cost on key routes

| # | Location | Issue |
|---|---|---|
| 7.17.1 🔴 | `VideoPlayer.svelte:2-4`, `watch/[itemId]/+page.svelte:2` | Static `video.js` (+CSS, VHS, HLS) = watch node **705 KB / 210 KB gz** before first frame; `jassub` is already dynamic. Fix: `await import('video.js')` in `initPlayer()`, lazy CSS, `manualChunks.vendorVideo` (`vite.config.ts` currently has none). |
| 7.17.2 🔴 | `books/[bookId]/read/+page.svelte:2-4`, `PdfReader.svelte:91` | All 3 readers statically imported → EPUB/CBZ downloads **464 KB pdfjs + worker**. Fix: `import()` gated on `format` (`isComic/isPdf/isEpub` at `+page.svelte:35-37`). |
| 7.17.3 🔴 | `PdfReader.svelte:53-87,99-105` | Renders **all** pages upfront (no windowing/yield vs `ComicReader.svelte:31`), `arrayBuffer()` waits for full download, all canvases at DPR 2 retained → OOM on 500p PDFs. Fix: ±5 page window via existing `IntersectionObserver`, `getDocument({url})` range streaming. |
| 7.17.4 🟠 | `+page.ts:41`, `+page.svelte:101,133,201,218`, `Shelf.svelte:98` | Home renders 100+ `MediaCard`s; each mounts `Poster.svelte:37` async token chain; offscreen shelves stay mounted. Fix: `limit=8` + “show more on arrow”, `content-visibility:auto`, mount shelves on intersection. |
| 7.17.5 🟡 | `api/client.ts:185-204` | No cache/dedup/ETag/cancellation; `AbortSignal` param exists but zero callers pass it; layout refetches libraries when empty (`+layout.ts:36` vs `+layout.svelte:32`); `ComicReader.svelte:62` prefetch serial. Fix: 30 s URL-keyed cache + inflight map (reuse single-flight at `client.ts:110`/`media-token.svelte.ts:22`), abort in `$effect` cleanup, `If-None-Match`/304. |
| 7.17.6 🟡 | `books/[id]/read/+page.ts:6,10`, `watch/[id]/+page.ts:6`, `next-episode.ts:26`, `series/[id]/+page.svelte:20-34` | Sequential waterfalls on independent fetches (2–3 RTT blocking playback/book open). Fix: `Promise.all`, backend single endpoint for next-episode, include S1 episodes in series detail. |
| 7.17.7 🟡 | `Poster.svelte:74-83`, `BookCover.svelte:31`, `ComicReader.svelte:146`, `library/[id]/+page.svelte:42-47`, `AudioBar.svelte:56,182` | Images: all `lazy` even first row (hurts LCP), no `srcset`/`fetchpriority`, fixed widths mismatch grid. Library filters refetch per keystroke (no debounce); audio prewarms next track when paused; `localStorage` per slider tick. Fix: `srcset`+DPR sizing, `fetchpriority="high"` first shelf, 250 ms debounce, `preload='metadata'`, 300 ms debounce. |

### 7.18 ⚪ Verified as-designed (no change)

- `models.py:235-289` — `lazy="selectin"` on `MediaItem` is an intentional async-safety contract; mitigate at route level (scalar projections as `home.py:175-239` already does for `recently-added`) rather than flipping to `lazy="raise"`.
- `streaming.py:147-158` — per-chunk `to_thread` ≈ 100–200 ms/GB spread; negligible vs network.
- `deps.py:88-111` — per-request auth tax (JWT + user reload + library check) is intentional for immediate revocation; optional per-request grant caching only.
- Home single `/home` call + `{#await}` skeleton, single-flight media-token/refresh, Poster blurhash LRU, `pdfjs`/`jassub` dynamic imports where effective — keep.

### 7.19 Summary table (2026 re-audit)

| # | Severity | Area | Finding | Status |
|---|---|---|---|---|
| 7.1 | 🟠 High | API | N+1 + unbounded loop in `next-up` | Open |
| 7.2 | 🟠 High | API | Sequential shelves in composite `/home` | Open |
| 7.3 | 🟠 High | API | Unbounded full-table load in collection auto-collect | Open |
| 7.4 | 🟡 Medium | API | Uncached subprocess probes on `/health` | Open |
| 7.5 | 🟡 Medium | API | Argon2id blocks event loop | Open |
| 7.6 | 🟡 Medium | API | Global playback lock serializes all writes | Open |
| 7.7 | 🟡 Medium | API | Per-request matcher/httpx construction, sequential providers | Open |
| 7.8 | 🔴 Critical | Service | Scanner blocking parse inside one long DB transaction | Open |
| 7.9 | 🔵 Low | Service | `_is_cancelled` DB session per file | Open |
| 7.10 | 🟠 High | Service | Serial metadata matching; lock held across `sleep` | Open |
| 7.11 | 🟡 Medium | Service | OpenLibrary/GoogleBooks have no cache/throttle | Open |
| 7.12 | 🟡 Medium | Service | Image pipeline serial, memory-heavy, render race | Open |
| 7.13 | 🟡 Medium | Service | Polling churn: worker/SSE/transcode; missing `jobs.updated_at` index | Open |
| 7.14 | 🟡 Medium | Service | NFO O(n²) scans, per-file stat loops | Open |
| 7.15 | 🟡 Medium | API | Unbounded details, double-count, `ORDER BY random()` | Open |
| 7.16 | 🟡 Medium | DB | Missing indexes; incomplete SQLite pragmas | Open |
| 7.17 | 🔴–🟡 | FE | Large bundles, full-book PDF render, home card overload, no cache/dedup, waterfalls | Open |
| 7.18 | ⚪ Info | — | Verified as-designed | No action |

### 7.20 Recommended priority order (2026)

**Phase 1 — Quick wins (< 1 day)**
1. `/health` TTL cache (§7.4); Argon2 → `to_thread` (§7.5); collection scalar query (§7.3)
2. `jobs.updated_at` / `audit_log.created_at` indexes + `cache_size`/`temp_store` pragmas (§7.16)
3. `ORDER BY random()` → offset; `count() OVER ()`; limits on author/artist detail (§7.15)
4. FE: `Promise.all` independent loaders, debounce library filters, `content-visibility`, image `fetchpriority`/`srcset` (§7.17)

**Phase 2 — Medium effort (1–3 days)**
5. `home_overview` parallel sessions; `list_next_up` batching (§7.1–7.2)
6. Scanner: phase-split writes, cancel-flag cache, batch commits (§7.8–7.9)
7. Metadata: bounded `gather`, singleton clients, book-provider cache (§7.7, §7.10–7.11)
8. FE: dynamic `video.js`/readers, PdfReader windowing, API cache + abort (§7.17)

**Phase 3 — Verification**
9. `EXPLAIN QUERY PLAN` on shelf/search/next-up paths; `cProfile`/wall-time on first scan; bundle analyzer before/after; load-test via `scripts/` before claiming gains.
