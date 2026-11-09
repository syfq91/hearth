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

### 1.1 🟠 Model-Level `lazy="selectin"` on `MediaItem` Causes Over-Fetching

**Location:** [`app/db/models.py` L235–L289](file:///home/syafiq/code/hearth/app/db/models.py#L235-L289)

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

**Recommendation:** Change all `MediaItem` type-specific relationships to
`lazy="select"` (the default lazy load, which is essentially no-op until
accessed) or `lazy="raise"`, then use explicit `selectinload()` options in each
query. The API endpoints already do this partially (e.g., `home.py` L68–73) but
the model-level eager loads fire _in addition to_ the query-level options,
resulting in duplicate work.

---

### 1.2 🟠 Deep `selectin` Chains on `Series` ↔ `Season` ↔ `Episode`

**Location:** [`app/db/models.py` L309–L385](file:///home/syafiq/code/hearth/app/db/models.py#L309-L385)

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

**Recommendation:** Same as 1.1 — use `lazy="raise"` or `lazy="select"` on the
model and load relationships explicitly per route.

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

### 1.4 🟡 Missing Composite Index on `user_item_state`

**Location:** [`app/db/models.py` L599–L621](file:///home/syafiq/code/hearth/app/db/models.py#L599-L621)

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

### 1.5 🟡 `recently-added` Fetches 5× the Limit Then Discards

**Location:** [`app/api/v1/home.py` L149](file:///home/syafiq/code/hearth/app/api/v1/home.py#L149)

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

### 1.6 🟡 Per-Card `session.get()` in `recently-added` Loop

**Location:** [`app/api/v1/home.py` L218](file:///home/syafiq/code/hearth/app/api/v1/home.py#L218)

```python
series_state = await session.get(UserItemState, (user.id, series_item.id))
```

This runs inside a `for` loop over each grouped card. For N series in the
response, it fires N individual SELECT queries.

**Impact:** O(N) DB round-trips per page load (up to 20).

**Recommendation:** Batch all series item IDs and use the existing
`watch_states()` helper once, then look up from the dict.

---

### 1.7 🔵 `_book_folders()` Loads All Book Files into Memory

**Location:** [`app/api/v1/items.py` L483–L504](file:///home/syafiq/code/hearth/app/api/v1/items.py#L483-L504)

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

### 1.8 ⚪ WAL Mode Set Only in Migrations, Not at Connection Time

**Location:** [`app/db/session.py` L22–L30](file:///home/syafiq/code/hearth/app/db/session.py#L22-L30)

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

### 2.1 🟠 Home Page Triggers a Waterfall of API Calls

**Location:** [`web/src/routes/+page.ts` L31–L49](file:///home/syafiq/code/hearth/web/src/routes/+page.ts#L31-L49)

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

### 2.2 🟡 `BaseHTTPMiddleware` for All Three Middleware Classes

**Location:** [`app/core/middleware.py` L22, L47, L92](file:///home/syafiq/code/hearth/app/core/middleware.py#L22-L92)

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

### 2.3 🟡 ETag Computation via JSON Serialization + SHA-256

**Location:** [`app/api/v1/items.py` L440–L442](file:///home/syafiq/code/hearth/app/api/v1/items.py#L440-L442)

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

### 2.4 🔵 Image Pipeline Instantiated Per Request

**Location:** [`app/api/v1/images.py` L68](file:///home/syafiq/code/hearth/app/api/v1/images.py#L68)

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

### 2.5 🔵 Person Search Uses `LIKE '%token%'` Without FTS

**Location:** [`app/api/v1/search.py` L183](file:///home/syafiq/code/hearth/app/api/v1/search.py#L183)

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

### 3.1 🟡 Scanner Probes Files Sequentially

**Location:** [`app/services/scanner/scanner.py` L200–L265](file:///home/syafiq/code/hearth/app/services/scanner/scanner.py#L200-L265)

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

### 3.2 🟡 Image Blurhash Computation in Process Pool

**Location:** [`app/services/images/pipeline.py` L127–L142](file:///home/syafiq/code/hearth/app/services/images/pipeline.py#L127-L142)

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

### 3.3 🔵 TMDB Rate Limiter Uses Sleep-Based Pacing

**Location:** [`app/services/metadata/tmdb.py`](file:///home/syafiq/code/hearth/app/services/metadata/tmdb.py)

The TMDB provider uses `_rate_lock` and `asyncio.sleep` to enforce a minimum
interval between requests (`tmdb_min_interval_seconds = 0.05`). This is
correct but simple — it serializes all TMDB requests, even reads from cache.

**Impact:** Metadata matching for a large scan is bottlenecked to ~20
requests/second even when the TMDB API allows more.

**Recommendation:** The current pacing is conservative and well-suited for a
home server. No change needed unless users report slow matching. If needed, use
a token-bucket rate limiter that allows bursts.

---

### 3.4 🔵 Streaming: File Handle Opened Per Chunk

**Location:** [`app/services/streaming.py` L147–L158](file:///home/syafiq/code/hearth/app/services/streaming.py#L147-L158)

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

### 4.1 🟡 Large JS Bundle on Reader Route (720 KB)

**Location:** `web/dist/_app/immutable/nodes/29.*.js` (720 KB)

This chunk corresponds to the book/comic reader page (pdfjs-dist). It is
code-split to its own route (good), but at 720 KB it's the largest chunk in the
bundle.

**Impact:** Slow first-load for the reader page on low-bandwidth connections.

**Recommendation:**
- Use `pdfjs-dist/webpack` or the worker-only entry point to avoid bundling the
  viewer UI.
- Ensure the PDF worker (`worker-BlALB9L6.js`, 112 KB) is loaded lazily.
- Consider lazy-loading the reader component inside the page with a loading
  spinner.

---

### 4.2 🟡 Both `@lucide/svelte` and `lucide-svelte` Are Dependencies

**Location:** [`web/package.json` L31, L35](file:///home/syafiq/code/hearth/web/package.json#L31-L35)

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

### 4.3 🟡 Second Shared Chunk at 464 KB

**Location:** `web/dist/_app/immutable/chunks/DKZe8jOO.js` (464 KB)

This is likely `video.js` or a combination of video.js + jassub. It's loaded as
a shared chunk which means it may be pulled in on routes that don't need it.

**Impact:** Increased JS parse time on the home page or library pages if this
chunk is eagerly loaded.

**Recommendation:** Verify which routes pull in this chunk. If it's only needed
on the player page, ensure it's behind a dynamic `import()`.

---

### 4.4 🔵 Home Page Makes 5+N API Calls (Waterfall)

See §2.1 — the frontend issue mirrors the backend recommendation. From the
client's perspective:

- First batch: 5 parallel calls (~200 ms)
- Second batch: N library-specific calls (~200 ms)
- Total: ~400 ms minimum for API alone

**Recommendation:** A single `/home` endpoint would reduce this to ~100 ms.

---

### 4.5 🔵 No HTTP Cache Headers on API Responses

The API sets `Cache-Control` on images (24h immutable) and streams (no-cache),
but JSON API responses (shelves, items, search) have no cache headers. The
browser re-fetches everything on every navigation.

**Impact:** Unnecessary API calls when the user navigates back to the home page.

**Recommendation:** Add short-lived cache headers (e.g.,
`Cache-Control: private, max-age=30`) to list endpoints. The ETag on item
detail is good but could be supplemented with `max-age` for the common case.

---

### 4.6 ⚪ Build Target `es2019` May Include Unnecessary Polyfills

**Location:** [`web/vite.config.ts` L41](file:///home/syafiq/code/hearth/web/vite.config.ts#L41)

The ES2019 target is intentional for TV browser compatibility. This is correct,
but it means modern browsers also receive downleveled syntax (e.g., optional
chaining compiled away).

**Recommendation:** No action needed — this is a deliberate trade-off
documented in the config. Consider adding browserslist-based differential
serving in the future.

---

## 5. Infrastructure

### 5.1 🔵 Docker HEALTHCHECK Uses `urllib` in Python

**Location:** [`docker/Dockerfile` L79–L80](file:///home/syafiq/code/hearth/docker/Dockerfile#L79-L80)

```dockerfile
CMD ["python", "-c", "import os, urllib.request; ..."]
```

Starting a Python interpreter for each health check (~100 ms startup) is heavier
than `curl` or `wget`.

**Impact:** ~100 ms per health check. With a 30s interval, this is negligible.

**Recommendation:** The image already has Python installed, so this avoids
adding `curl`. Acceptable as-is.

---

### 5.2 ⚪ No `mmap_size` Pragma for Large Databases

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
| 1.1 | 🟠 High | DB | `MediaItem` selectin over-fetching (10 relations) | Open |
| 1.2 | 🟠 High | DB | Recursive selectin cascade on Series/Season/Episode | Open |
| 1.3 | 🟡 Medium | DB | Pool size 50+50 for SQLite | ✅ Resolved |
| 1.4 | 🟡 Medium | DB | Missing index on `user_item_state` for continue-watching | Open |
| 1.5 | 🟡 Medium | API | `recently-added` fetches 5× then discards | Medium |
| 1.6 | 🟡 Medium | API | Per-card `session.get()` in recently-added loop | Easy |
| 1.7 | 🔵 Low | API | `_book_folders()` materializes all books in memory | Medium |
| 1.8 | ⚪ Info | DB | WAL set only in migration | None |
| 2.1 | 🟠 High | API+FE | Home page = 5+N HTTP requests | Medium |
| 2.2 | 🟡 Medium | API | `BaseHTTPMiddleware` for all middleware | Easy |
| 2.3 | 🟡 Medium | API | ETag via JSON + SHA-256 per request | Easy |
| 2.4 | 🔵 Low | API | ImagePipeline created per request | Easy |
| 2.5 | 🔵 Low | API | Person search uses ILIKE | Low |
| 3.1 | 🟡 Medium | Service | Scanner hashes sequentially | Easy |
| 3.2 | 🟡 Medium | Service | Blurhash IPC overhead | Easy |
| 3.3 | 🔵 Low | Service | TMDB rate limiter serializes cached reads | Low |
| 3.4 | 🔵 Low | Service | Thread-per-chunk file streaming | Low |
| 4.1 | 🟡 Medium | FE | 720 KB reader chunk | Medium |
| 4.2 | 🟡 Medium | FE | Duplicate lucide icon packages | Easy |
| 4.3 | 🟡 Medium | FE | 464 KB shared chunk | Easy |
| 4.4 | 🔵 Low | FE | Home page waterfall (mirrors 2.1) | — |
| 4.5 | 🔵 Low | FE | No cache headers on JSON responses | Easy |
| 4.6 | ⚪ Info | FE | ES2019 target includes polyfills | None |
| 5.1 | 🔵 Low | Infra | Python healthcheck startup cost | None |
| 5.2 | ⚪ Info | Infra | No mmap_size pragma | Trivial |

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
