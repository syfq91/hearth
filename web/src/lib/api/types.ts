/**
 * Type-only access to the generated OpenAPI client.
 *
 * `packages/client/src/schema.d.ts` is produced by `openapi-typescript` from
 * `docs/openapi.json` (see `packages/client/package.json` and
 * `scripts/export_openapi.py`); it contains no runtime code, so every import
 * from this module must be type-only.
 *
 * The generated document exposes whole namespaces (`paths`, `components`,
 * `operations`); individual schemas are reached through {@link S}:
 *
 * ```ts
 * import type { S } from '../api/types';
 * let album: S['AlbumSummary'];
 * ```
 */
import type { components, operations, paths } from '@hearth/client';

export type { components, operations, paths };

/** Shorthand for the generated JSON-schema component namespace. */
export type S = components['schemas'];

/* Frequently used aliases (kept short for call sites). */
export type TokenPair = S['TokenPair'];
export type Credentials = S['Credentials'];
export type RefreshRequest = S['RefreshRequest'];
export type UserResponse = S['UserResponse'];
export type HealthResponse = S['HealthResponse'];
export type VersionResponse = S['VersionResponse'];
export type MediaTokenResponse = S['MediaTokenResponse'];
export type HomeShelf = S['HomeShelf'];
export type NextUpShelf = S['NextUpShelf'];
export type ItemSummary = S['ItemSummary'];
export type NextUpEpisode = S['NextUpEpisode'];
export type AlbumSummary = S['AlbumSummary'];
export type LibrarySummary = S['LibrarySummary'];
