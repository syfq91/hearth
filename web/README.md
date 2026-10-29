# Hearth web UI

The first usable Hearth frontend: a **SvelteKit single-page app** (Svelte 5,
TypeScript strict, Vite) selected in `docs/FRONTEND_DECISION.md` and built
during Phase 9.

## How it fits together

- **Generated API types**: `src/lib/api/types.ts` re-exports
  `packages/client/src/schema.d.ts` (produced by `openapi-typescript` from
  `docs/openapi.json`) through the `@hearth/client` alias in `tsconfig.json`.
  The alias also exists in `vite.config.ts`; imports must stay type-only.
- **API layer**: `src/lib/api/client.ts` — same-origin `/api/v1` fetch with
  bearer auth, single-flight refresh on 401, and the unified error envelope
  surfaced as `ApiError`.
- **Session**: `src/lib/auth.svelte.ts` — Svelte 5 runes state; tokens persist
  in `localStorage` (`hearth.access` / `hearth.refresh`).
- **Serving**: `npm run build` writes `dist/`; FastAPI mounts it at `/` with
  an SPA fallback (`app/core/spa.py`) when the directory exists, and the
  Docker image always contains it (`docker/Dockerfile`, `web` stage).
- **Build target**: `es2019` so the bundle parses on 2020+ TV browsers
  (Chromium M69+; see the engine table in `docs/FRONTEND_DECISION.md` §4.3).

## Scripts

```bash
npm ci            # install (runs svelte-kit sync via prepare)
npm run dev       # dev server with HMR (API must be reachable; set a proxy or run on :8000)
npm run check     # svelte-check + TypeScript
npm run build     # production build → dist/
npm run preview   # preview the production build
```
