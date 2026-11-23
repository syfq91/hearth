# Backend bug audit

**Date:** 2026-11-23
**Scope:** `app/` — all 119 Python files (API routes, core/auth/security, services, DB layer, jobs/worker, CLI, plugins), with cross-references into `docs/development/api.md`, `docs/development/architecture.md`, the generated schema, and the installed Starlette/httpx behavior.
**Method:** Manual code review in three parallel passes (auth/core security, API routes, services/db), followed by direct verification of every finding against the source. Security analysis applied the bug-bounty skill's impact bar ("can an attacker do this right now against a real user and cause real harm?") and its checklists for auth bypass, IDOR, SSRF, path traversal, rate limiting, and race conditions; FastAPI-specific review applied the FastAPI skill's conventions (dependency declarations, async vs sync path operations, response models, router wiring).
**Baseline:** `uv run ruff check` → all checks passed. `pytest` and `mypy` binaries are not installed in this environment, so no automated test/type run was possible; findings were verified by reading code and library behavior directly.

---

## 1. Executive summary

14 concrete bugs were found: **1 High**, **7 Medium**, **6 Low**.

| ID | Finding | Severity | Primary location |
| --- | --- | --- | --- |
| BE-001 | First-admin setup is a TOCTOU race — attacker can create a backdoor admin | High | `api/v1/auth.py:167-209` |
| BE-002 | Per-username login throttle allows indefinite remote lockout | Medium | `api/v1/auth.py:222-247` |
| BE-003 | Request-body size limit bypassable with chunked transfer encoding | Medium | `core/middleware.py:27-50` |
| BE-004 | Non-ASCII book titles return HTTP 500 on download | Medium | `api/v1/stream.py:290-292` |
| BE-005 | Partial playlist reorder leaves negative positions / corrupts ordering | Medium | `api/v1/playlists.py:384-395` |
| BE-006 | Partial collection reorder leaves duplicate positions / non-deterministic order | Medium | `api/v1/collections.py:336-348` |
| BE-007 | Artwork-download SSRF — host allow-list bypassed by following redirects | Medium | `services/images/pipeline.py:382,387-395` |
| BE-008 | NFO artwork paths are not confined to the library/media root | Medium | `services/metadata/local.py:289-304` |
| BE-009 | `GET /items/{id}?fields=<non-ASCII>` returns 500 | Low | `api/v1/items.py:463-465` |
| BE-010 | Login limiter never releases checked-only keys — unbounded memory growth | Low | `core/ratelimit.py:33-45` |
| BE-011 | Password reset does not stop live transcode sessions | Low | `api/v1/users.py:402-430` |
| BE-012 | SSE stream discloses job payloads from inaccessible libraries | Low | `api/v1/events.py:88-98,129-147` |
| BE-013 | Comic-page endpoint skips token scope and path-containment checks | Low | `api/v1/books.py:603-632` |
| BE-014 | Collection listings expose cross-library item counts | Low | `api/v1/collections.py:111-139` |

---

## 2. High severity

### BE-001 — First-admin setup is a TOCTOU race

**Location:** `app/api/v1/auth.py:167-209` (count at `:176-181`, creation/commit at `:186-208`); model at `app/db/models.py:29-38`.

**Problem.** The unauthenticated `POST /api/v1/auth/setup` endpoint checks `SELECT count(*)` over all users and, if zero, creates an admin — but does so without a lock, unique guard, or re-check at commit time:

```python
user_count = await session.scalar(select(func.count()).select_from(User))
if user_count:
    raise ConflictError(...)

user = User(
    username=username,
    password_hash=await hash_password_async(payload.password),  # tens of ms
    role="admin",
)
session.add(user)
...
await session.commit()
```

The Argon2id hash between the check and the insert widens the race window. The `users` table enforces uniqueness only on `username`, so two concurrent requests with different usernames both succeed.

**Impact.** On a freshly deployed, network-reachable server, an attacker who continuously fires `/setup` with their own credentials can win the race against the legitimate operator and obtain a permanent admin account — full server compromise. The window is publicly advertised by `GET /api/v1/auth/setup` (`:154-164`), which reports `setup_required`.

**Suggested fix.** Make the check-and-create atomic. Options: acquire the global setup lock (the repo already has `app/core/locks.py`) around the count and insert; or add a single-row "setup completed" marker with a unique constraint and insert it in the same transaction; or use a conditional `INSERT ... WHERE NOT EXISTS` and treat a constraint violation as `409`. Verify the count again inside the transaction immediately before commit.

---

## 3. Medium severity

### BE-002 — Per-username login throttle allows indefinite remote lockout
**Location:** `api/v1/auth.py:222-247`; limiter at `core/ratelimit.py:47-63`.
The limiter tracks `user:<username>` with a 5-attempt/60s sliding window. An attacker who knows a username sends five wrong passwords; from then on `retry_after(user_key) > 0` at `:225` short-circuits **every** login — including one with the correct password — with 429. `limiter.reset()` at `:247` only runs after a successful authentication, which the victim can never reach. The attacker sustains the window with roughly one bad request every 12 seconds. This is a concrete availability attack on any named account, including the sole admin. Consider separating "bad password" throttling from successful-login eligibility, applying progressive backoff, or adding a CAPTCHA/out-of-band path for the legitimate user.

### BE-003 — Request-body size limit bypassable with chunked transfer encoding
**Location:** `core/middleware.py:27-50`.
`RequestSizeLimitMiddleware` reads only `headers.get("content-length")` (`:33`). A client using `Transfer-Encoding: chunked` omits `Content-Length`, so the check is skipped and Starlette buffers the entire body when parsing JSON on endpoints such as the unauthenticated `POST /auth/login`/`setup`. An unauthenticated attacker can drive memory exhaustion with an arbitrarily large streamed body. The limit must be enforced while consuming the ASGI receive stream (counting bytes per `http.request` message), not inferred from a single header.

### BE-004 — Non-ASCII book titles return 500 on download
**Location:** `api/v1/stream.py:290-292`, triggered by `api/v1/books.py:578,586`.
`download_book_file` builds `filename = f"{item.title}.{fmt}"` from the catalog title and passes it as `download_filename`. `stream_media_file` places it raw into a `Content-Disposition` header. Starlette encodes header values as latin-1, so a title containing Chinese/Japanese/Arabic characters raises `UnicodeEncodeError` before a response is built — every download of such a book fails with 500. Titles come from filenames and online providers (TMDB, Open Library), so non-Latin titles are common. Use RFC 5987 encoding (`filename*=UTF-8''...`) with an ASCII fallback.

### BE-005 — Partial playlist reorder corrupts ordering
**Location:** `api/v1/playlists.py:384-395`; request model at `:93-98` (`item_ids: list[str] = Field(min_length=1)`).
The two-phase update first sets **every** `PlaylistItem.position` to `-(idx+1)`, then assigns `0..n-1` only to IDs present in `body.item_ids`. The model does not require the full list, so a natural "move this one item to the top" request with a subset leaves omitted items at negative positions permanently. Since results are ordered by `position ASC`, those items sort ahead of all reordered ones and the API returns negative positions. The UNIQUE constraint prevents a DB error but not the corruption. Either require the complete ordered list, or assign positions to omitted items in their existing relative order after the named ones.

### BE-006 — Partial collection reorder duplicates positions
**Location:** `api/v1/collections.py:336-348`.
Same pattern as BE-005, but items not named in `body.item_ids` keep their original positions while named items receive `0..n-1`. Because `CollectionItem.position` is indexed but **not unique** (`db/models.py:676`), no constraint error occurs; multiple rows share a position and `_get_collection_detail` orders only by `position ASC`, making the order non-deterministic and different from both the prior and requested order.

### BE-007 — Artwork-download SSRF via redirects
**Location:** `services/images/pipeline.py:382` (`follow_redirects=True`), checks at `:387-395`; documented invariant at `:7-10`.
The module docstring states: *"remote artwork is only fetched over http(s) from an allow-listed host ... redirects are not followed."* The code validates only the **initial** hostname, then builds `httpx.AsyncClient(follow_redirects=True)`. An allow-listed URL (e.g. on `books.google.com` or `image.tmdb.org`) can respond `302 Location: http://169.254.169.254/...` or `http://127.0.0.1:...`, and `client.stream("GET", url)` follows it. The allow-list is not a destination restriction.

This is reachable from external data: `plugins/googlebooks/plugin.py:226-236` copies `volumeInfo.imageLinks[...]` verbatim from the Google Books API into `ProviderImage.source_url`, later passed to `_download` (`pipeline.py:248-249`). A tampered provider response or any plugin returning an attacker-chosen URL on an allowed host yields blind SSRF from the worker/API process (internal GETs, port probing) even when the response is ultimately rejected for content type. Fix: set `follow_redirects=False` (matching the docstring) or re-validate each `Location` against the scheme/host allow-list before following.

### BE-008 — NFO artwork paths are not confined to the library/media root
**Location:** `services/metadata/local.py:289-304` → `services/images/pipeline.py:250-251,418-433`.
Unlike streaming, attachments, and subtitles — which validate paths with `path_is_within_roots` — NFO artwork accepts a plain filesystem path from text:

```python
candidate = Path(text)  # e.g. "/etc/..." or "../../other/private.jpg"
if not candidate.is_absolute():
    candidate = folder / candidate
add(kind, candidate)  # ProviderImage(file_path=str(candidate.resolve()))
```

`ImagePipeline.store_image` then calls `_read_local(Path(provider_image.file_path))`, which opens that path and copies it into `/config/cache/images`. Any image-readable file on the mounted filesystem, including another library's artwork outside the current root, can be copied and served through the authenticated `/images/{id}` endpoint — an access-control bypass for cross-library data. Resolve the candidate and enforce containment within the registered library roots before storing it.

---

## 4. Low severity

**BE-009 — Non-ASCII `fields` query returns 500** — `api/v1/items.py:463-465`. `etag_raw` includes the raw `fields` query string (constrained only by `max_length=500`) and is hashed with `.encode("ascii")`; `?fields=résumé` raises `UnicodeEncodeError`. Use UTF-8 (or validate the parameter's character set).

**BE-010 — Rate limiter memory leak** — `core/ratelimit.py:33-45`. `_prune()` removes an empty deque (`:41-43`) then immediately recreates and re-inserts an empty deque for the same key (`:44-45`). Every `retry_after()` call (two per login attempt) leaves a permanent entry. `reset()` clears only `user_key` after success; IP keys and never-hit username keys persist forever. Varying usernames or `X-Forwarded-For` (when proxy headers are trusted) grows `_events` without bound.

**BE-011 — Password reset does not stop live transcode sessions** — `api/v1/users.py:402-430` vs `:357`. `reset_password()` bumps `token_version` and revokes refresh rows but never calls `get_session_manager(settings).stop_user_sessions(user.id)`, which the account-disable path does. Active ffmpeg/HLS sessions keep running, occupying CPU/transcode capacity until idle timeout, contradicting the endpoint's "revoke every existing session" docstring. (No data leak: HLS segments revalidate the token's `token_version`.)

**BE-012 — SSE discloses cross-library job metadata** — `api/v1/events.py:88-98,129-147`. The DB-polling path emits `JobResponse.from_row(job)` for all pending/running/updated jobs to every connected user. `JobResponse` includes `payload` and `result` (`api/v1/jobs.py:29-45`); a `scan.library` payload can carry `library_id` and target file paths (`services/scanner/jobs.py:49-52`) and failed jobs include `error`. The per-user filter at `:119-122` applies only to in-memory broadcasts, not these DB-sourced events. A library-restricted user learns IDs and paths for libraries they cannot access.

**BE-013 — Comic-page endpoint skips scope/path checks** — `api/v1/books.py:603-632`. It uses `ViewerDep` and checks library access, but unlike sibling endpoints (`attachments.py:65-92`, `subtitles.py:55-75`, `stream.py:242-272`) it never checks `viewer.item_id` against the requested book and never calls `path_is_within_roots`. A media token scoped to item A can fetch pages of comic B, and a stale/tampered `MediaFile.path` is opened unchecked. The caller still needs library access, so this is a scope-invariant violation rather than a privilege escalation.

**BE-014 — Collection listings expose cross-library item counts** — `api/v1/collections.py:111-139`. `item_count` is a global `COUNT(CollectionItem)` with no `LibraryAccess` or `is_visible()` filter, unlike the detail endpoint (`:386-394`). A user with access to none of a collection's items still learns how many it contains, including items in hidden libraries.

---

## 5. Recommended fix order

1. **BE-001** — unauthenticated remote admin creation; fix first (atomic setup guard).
2. **BE-002, BE-003** — remotely exploitable availability/DoS controls (login lockout, chunked-body limit bypass).
3. **BE-007, BE-008** — security-control defeats (SSRF via redirect, arbitrary local image read); both are small, localized changes.
4. **BE-004, BE-005, BE-006, BE-009** — user-facing 500s and silent data corruption.
5. **BE-010–BE-014** — resource hygiene and metadata-disclosure cleanup.

## 6. Regression-test recommendations

- Concurrency test: fire two `/auth/setup` requests simultaneously; assert exactly one succeeds (covers BE-001).
- Throttle test: saturate `user:<victim>` from one client, then assert the victim can still log in with correct credentials (BE-002).
- Send a chunked request larger than `max_request_body_bytes` and assert 413 (BE-003).
- Download a book with a non-ASCII title and assert a valid `Content-Disposition: filename*` (BE-004).
- Reorder a playlist/collection with a subset of IDs and assert all remaining positions are non-negative and unique (BE-005/BE-006).
- Assert redirect responses cannot leave the allow-listed host and that redirects are disabled or re-validated (BE-007).
- Point an NFO `<thumb>` outside the library root and assert it is rejected (BE-008).

## 7. Areas reviewed with no findings

Token cryptography and validation (`tokens.py`, `secrets.py`); refresh-token rotation, reuse detection, and hashed storage; media-token scope enforcement in stream/HLS/image/subtitle consumers; password hashing and unknown-username timing equalization; authorization reading role/id from the database rather than JWT claims; 403-vs-404 library hiding; read-only health check that never creates/migrates the DB; error envelope with no stack traces or secret leakage; CORS/security-header defaults for normal responses; SPA static serving; job claim/retry/cancellation/crash-recovery logic; SQLAlchemy 2 async session lifecycle, UTC/UUID handling, WAL pragmas, and migrations `0001`–`0014`; all ffmpeg/ffprobe subprocess calls (argument lists, no shell, blocking reads offloaded); transcode command construction and circuit breaker; CLI and plugin lifecycle.
