# Hearth Frontend Decision (Phase 9, Step 1)

> **Status: RECOMMENDATION PENDING HUMAN APPROVAL.** Per the implementation plan,
> Phase 9 Step 1 ends with a recommendation; no frontend code is written until
> this document is approved. Date: 2026-10-28.

## 1. Context

Hearth's backend is complete and API-first through Phase 8: a stable OpenAPI
schema (`docs/openapi.json`, 102 paths / 118 operations), a generated typed
client artifact (`packages/client/src/schema.d.ts` via `openapi-typescript`),
HLS fMP4 transcoding with `hls.js`-compatible playlists, native book APIs with
EPUB/PDF/CBZ readers (`docs/READER_INTEGRATION.md`), and SSE events. Phase 9
must choose a frontend framework and build the first usable UI against that
API, then prove it with Playwright e2e tests in CI, a `docker compose up` →
watch-a-video first-run flow, and a Lighthouse performance score ≥ 80 on the
home page.

The plan requires this decision to be made explicitly, on the record, before
any scaffolding exists — so that the choice is never re-litigated mid-build.

## 2. Criteria (from the plan)

1. **HLS playback support**
2. **D-pad and remote navigation on TV browsers** (10-foot mode)
3. **Ecosystem for EPUB and PDF readers**
4. **Bundle size**
5. **Typed client generation story**
6. **Long-term maintainability for a small project**

## 3. Options compared

- **Option A — React 19 + Vite** (client-only SPA, React Router, TanStack Query)
- **Option B — SvelteKit as an SPA** (`adapter-static`, no SSR)
- **Option C — Vue 3 + Vite** (Vue Router, Pinia, TanStack Query for Vue)

Runners-up considered and set aside: SolidJS (excellent runtime, much smaller
ecosystem and library surface), Angular (framework weight and ceremony
disproportionate for a household project), Astro (great for content sites, not
an interactive player app), vanilla TS + Web Components (maximum control,
maximum hand-rolled code for state, routing and data fetching across ~15
screens).

## 4. Facts that are framework-agnostic (shared ground)

These hold regardless of the framework choice, and they shrink the real
difference between the options.

### 4.1 The heavy dependencies are shared and lazy-loadable

Measured locally on 2026-10-28 (esbuild 0.28.2, `--bundle --minify`, ESM,
`target=es2020`, gzip -9; minimal "hello" entry per package — see §8):

| Package (version) | Minified | Gzip | Loaded |
| --- | --- | --- | --- |
| React 19.3.1 + react-dom | 217.6 KB | **67.4 KB** | app shell |
| Vue 3.5.44 (runtime only) | 64.4 KB | **25.8 KB** | app shell |
| Svelte 5.57.3 (compiled) | 43.0 KB | **16.0 KB** | app shell |
| hls.js 1.7.4 | 576.2 KB | 181.7 KB | player route only (dynamic import) |
| foliate-js 1.0.1 `epub.js` | 27.4 KB | 10.5 KB | EPUB reader only |
| foliate-js 1.0.1 `view.js` (reader UI) | 181.5 KB | 63.6 KB | EPUB reader only |
| pdfjs-dist 6.4.299 | 458.4 KB | 137.6 KB | PDF reader only |

The three reader/player dependencies dominate the payload and are identical
under every option; they are route-level dynamic imports either way. The
framework delta that actually reaches the first paint of the home page is
16 KB (Svelte) vs 25.8 KB (Vue) vs 67.4 KB (React) gzipped.

### 4.2 HLS playback is a `<video>` + hls.js problem, not a framework problem

Hearth emits HLS fMP4 (`master.m3u8` / `media.m3u8` / `init.mp4` / `seg_N.m4s`),
auth'ed by media tokens in the query string — exactly what `hls.js` consumes.
`hls.js` attaches to any HTMLMediaElement; Safari uses native HLS via the same
`<video>` src. The existing developer player (`tools/testplayer/index.html`)
already proves this stack works against the API with zero framework. Any
framework hosts a thin, testable `useHlsPlayer` module equally well; the
plan's Phase 7 test-player upgrade (hls.js 1.5.17 → 1.7.4 now on npm) is the
reference implementation.

### 4.3 TV browsers are an engine-target problem, not a framework problem

Samsung's published web engine table (fetched 2026-10-28, developer.samsung.com
smarttv web-engine-specifications):

| Model year | Platform | Engine |
| --- | --- | --- |
| 2026 | Tizen 10.0 | Chromium M130 |
| 2024 | Tizen 8.0 | Chromium M108 |
| 2022 | Tizen 6.5 | Chromium M85 |
| 2020 | Tizen 5.5 | Chromium M69 |
| 2018 | Tizen 4.0 | Chromium M56 |
| 2017 and older | Tizen ≤ 3.0 | Chromium ≤ M47 / WebKit |

Implications for **any** option: build with `build.target` at or below
ES2019 (so syntax like optional chaining is downleveled for Chromium 69–85
TVs), avoid post-2020 platform APIs without feature detection, prefer
`fetch` + `EventSource` (Phase 8 already added query-string token auth so
`EventSource` works), and treat 2019-and-older TVs as best-effort. D-pad
handling is native `KeyboardEvent` arrow-key handling plus focus management —
all three frameworks drive the same DOM APIs.

### 4.4 The typed client is generated once and shared

`scripts` + `packages/client` already run `openapi-typescript` against
`docs/openapi.json` to emit `src/schema.d.ts` (regenerated and diff-checked in
CI). On top of that:

- `openapi-fetch` (same maintainers) gives a framework-agnostic typed fetch
  client from those types — identical code in React, Vue or Svelte.
- `orval` can generate TanStack Query hooks per framework — it supports
  React Query, Vue Query **and** Svelte Query (orval.dev docs), so even the
  framework-specific codegen path exists for all three options.

Conclusion: this criterion is a near-tie by construction; React's edge is only
that TanStack Query's React bindings are the most battle-tested.

## 5. Criterion-by-criterion comparison

| Criterion | A: React + Vite | B: SvelteKit SPA | C: Vue + Vite |
| --- | --- | --- | --- |
| 1. HLS playback | Strong (tie) | Strong (tie) | Strong (tie) |
| 2. TV D-pad / 10-foot | **Strong** — mature TV-specific libs (e.g. `@noriginmedia/norigin-spatial-navigation`, widely used for smart-TV apps); React is the mainstream Tizen/webOS target framework (Samsung's own docs ship React tooling tips) | Adequate — no dedicated spatial-nav library; build a small shared focus manager (fine, but hand-rolled) | Adequate — no dedicated spatial-nav library; same hand-rolled module |
| 3. EPUB/PDF readers | **Strong** — community wrappers exist for foliate-js (e.g. `react-book-reader`) and epub.js; React 19 has first-class custom-element support (foliate-js is web components); pdf.js is canvas-hosting in any framework | Adequate — foliate-js/pdf.js integrate fine (web components are native to Svelte), but no wrapper layer; all glue code is ours | Adequate — Vue has long-standing first-class web component interop; same situation as Svelte |
| 4. Bundle size (app shell, gzip) | Weak — 67.4 KB | **Strong** — 16.0 KB | Good — 25.8 KB |
| 5. Typed client story | **Strong** — `openapi-fetch` + orval/TanStack Query hooks, largest tooling surface | Good — `openapi-fetch` + orval Svelte Query; smaller surface | Good — `openapi-fetch` + orval Vue Query |
| 6. Maintainability (small project) | Good — largest ecosystem and library availability; client-only SPA API surface is stable (React's churn is concentrated in server components, which we do not use); most examples/assistant training data; heavier runtime is a non-issue at this scale | Good — smallest and calmest runtime, compiler-based, official SvelteKit; but two major paradigm rewrites in five years (Svelte 4 → 5 runes) and a smaller library ecosystem raise long-tail risk for a project maintained by one person | **Strong** — small stable core, official router/devtools, gentle upgrade path, excellent docs; middle-sized ecosystem |

### 5.1 Criterion 1 — HLS playback: tie

`hls.js` (1.7.4, ~10.3 M downloads/week) plus native-Safari fallback is the
same module under all three frameworks. Not a differentiator. The only
framework-sensitive work is React's `StrictMode` double-invoke hygiene for
player effects, which is a known, documented pattern.

### 5.2 Criterion 2 — TV D-pad and remote navigation: React advantage

The real work is identical everywhere: arrow-key `KeyboardEvent` handling,
roving `tabindex`, focus visibility (`:focus-visible` rings), scroll-into-view,
and an Exit/Back key handler per TV platform. What differs is available
batteries: React has a mature, TV-proven spatial navigation library
(Norigin), while Vue and Svelte would start from a ~300-line in-house focus
manager. The plan requires a "focus-managed 10-foot mode" — under React we can
either adopt the proven library or write the module; under Vue/Svelte writing
it is mandatory. **Decision detail:** the build will implement a small
framework-agnostic focus-manager hook first (so TV mode is testable and not
locked to one library), with Norigin as an optional upgrade — the React
ecosystem is the safety net, not a hard dependency.

### 5.3 Criterion 3 — Reader ecosystem: slight React advantage, effectively neutral

`docs/READER_INTEGRATION.md` (Phase 6) already fixes the integration contract:
**foliate-js** for EPUB (CFI locators, 1500 ms debounced progress PUTs),
**pdf.js** for PDF, and a virtualized canvas pipeline for CBZ pages against
`/api/v1/books/{id}/pages/{n}`. Both libraries are framework-agnostic ES
modules / web components; React additionally has community wrappers if we want
one. All three frameworks can host them; React 19's custom-element property
support removes the one historical React/web-components friction point.

### 5.4 Criterion 4 — Bundle size: Svelte wins, but it is the smallest lever

Measured numbers in §4.1: Svelte 16.0 KB < Vue 25.8 KB < React 67.4 KB
(gzip, app shell). Against that, hls.js alone is 181.7 KB and pdf.js 137.6 KB
— both lazy, both shared. For the plan's Lighthouse ≥ 80 gate, the levers that
matter are route-level code splitting, lazy reader/player imports, image
placeholder/BlurHash usage and avoiding render-blocking CSS — all available in
every option. React's ~51 KB gzip penalty over Svelte is real but
absorbed easily at this scale; it is not a reason to pick a framework alone.

### 5.5 Criterion 5 — Typed client generation: near-tie

The repo's generation story (`openapi-typescript` → `schema.d.ts`, CI-checked)
is preserved unchanged by all three options; `openapi-fetch` gives typed
`paths`/`request`/`response` calls with no framework coupling; `orval` covers
React Query, Vue Query and Svelte Query if we want generated hooks. React's
only edge is ecosystem maturity of TanStack Query bindings.

### 5.6 Criterion 6 — Long-term maintainability: Vue's strength, React's safety net

For a single-maintainer, self-hosted project that an AI agent maintains across
years: Vue's small stable core and gentle upgrade path are the calmest choice;
React's advantage is depth — every library, pattern and troubleshooting path
exists, and the client-only SPA surface (no server components, no meta-framework
magic) is the most stable part of the React world. Svelte is the most pleasant
to write but has the least buffer: two major paradigm shifts in five years and
a smaller library ecosystem mean more hand-rolled code over the life of the
project.

## 6. Scorecard

Weights are equal; scores are 1–5 per criterion.

| Criterion | A: React + Vite | B: SvelteKit SPA | C: Vue + Vite |
| --- | --- | --- | --- |
| HLS playback | 5 | 5 | 5 |
| TV D-pad / remote nav | 5 | 3 | 3 |
| EPUB/PDF readers | 5 | 4 | 4 |
| Bundle size | 3 | 5 | 4 |
| Typed client story | 5 | 4 | 4 |
| Maintainability (small project) | 4 | 3 | 5 |
| **Total (30)** | **27** | **24** | **25** |

## 7. Recommendation

**Option A — React 19 + Vite, as a client-only SPA** (no SSR), with:

- **TypeScript** throughout, strict mode.
- **React Router** for routing; route components lazy-loaded so hls.js,
  foliate-js and pdf.js never enter the initial bundle.
- **TanStack Query** for server state (cache, retry, invalidation) on top of
  the generated types from `packages/client` + `openapi-fetch`.
- **Build target ES2019** (Chromium M69+ covers 2020-and-newer TVs), plus
  feature detection for anything newer; `prefers-reduced-motion` honored.
- **A framework-agnostic focus manager** (`useSpatialFocus`) implementing
  roving tabindex + arrow-key geometry, so 10-foot mode is unit-testable and
  portable rather than coupled to a third-party library.
- **Auth via bearer tokens in memory + refresh rotation**, matching
  `docs/API.md`; media tokens minted for `<video>` / `<img>` query strings.
- **Served by FastAPI** from `web/dist` at `/` with an SPA fallback, so
  `docker compose up` yields the UI at the same origin as the API (no CORS
  in production).

### Why not the others (reversal triggers)

- **Vue (25/30)** — the runner-up and a perfectly defensible choice; pick Vue
  instead if the maintainer prefers its ergonomics or wants the smallest
  mainstream runtime. Revisit if React's API churn ever affects client-only
  apps (it currently does not).
- **SvelteKit (24/30)** — best raw bundle size and a lovely developer
  experience; chosen only if bundle size becomes the binding constraint or if
  Svelte's ecosystem risk is judged acceptable. `adapter-static` SPA mode also
  adds a meta-framework layer we would use for very little.

### Soft factor recorded honestly

An autonomous coding agent maintains most of this codebase; React has by far
the deepest documentation, example and troubleshooting corpus, which reduces
the cost and error rate of future agent-driven changes. This factor was not
part of the plan's criteria and was weighted only into the maintainability
row.

## 8. Measurement methodology (reproducible)

Framework/dependency sizes were measured on 2026-10-28 with a scratch project
(`react`, `react-dom`, `vue`, `svelte`, `hls.js`, `foliate-js`, `pdfjs-dist`
from npm; esbuild 0.28.2; `--bundle --minify --format=esm --target=es2020`,
`process.env.NODE_ENV` defined to `"production"`; gzip level 9). Each entry
was a minimal valid program: `createApp(...).mount()` for Vue, a compiled
`<div>hello {name}</div>` Svelte component, `createRoot().render()` for
React, and direct module imports for hls.js / foliate-js / pdfjs-dist.
Numbers are indicative of app-shell cost, not of the finished app's total
bytes; the finished bundle will be measured during the build (Lighthouse gate).

## 9. Approval

Per the plan: **stop and wait for approval before building.** Approval of this
document unlocks Phase 9 Step 2 (scaffold in `web/`, screens, responsive +
10-foot layout, accessibility, Playwright e2e in CI).
