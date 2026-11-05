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
export type SetupStatusResponse = S['SetupStatusResponse'];
export type HealthResponse = S['HealthResponse'];
export type VersionResponse = S['VersionResponse'];
export type MediaTokenResponse = S['MediaTokenResponse'];
export type HomeShelf = S['HomeShelf'];
export type BookShelf = S['BookShelf'];
export type NextUpShelf = S['NextUpShelf'];
export type ItemPage = S['ItemPage'];
export type ItemDetail = S['ItemDetail'];
export type SeriesDetail = S['SeriesDetail'];
export type SeasonSummary = S['SeasonSummary'];
export type SeasonDetail = S['SeasonDetail'];
export type EpisodeDetail = S['EpisodeDetail'];
export type EpisodeSummary = S['EpisodeSummary'];
export type PlaybackInfoResponse = S['PlaybackInfoResponse'];
export type AlbumListResponse = S['AlbumListResponse'];
export type AlbumDetail = S['AlbumDetail'];
export type TrackSummary = S['TrackSummary'];
export type BookListResponse = S['BookListResponse'];
export type BookListItem = S['BookListItem'];
export type BookDetail = S['BookDetail'];
export type BookProgressResponse = S['BookProgressResponse'];
export type SearchResponse = S['SearchResponse'];
export type AdminSystemInfoResponse = S['AdminSystemInfoResponse'];
export type TranscodeSessionResponse = S['TranscodeSessionResponse'];
export type JobResponse = S['JobResponse'];
export type AuditEntry = S['AuditEntry'];
export type UserDetail = S['UserDetail'];
export type LibraryDetail = S['LibraryDetail'];
export type UnmatchedItemResponse = S['UnmatchedItemResponse'];
export type SearchResultResponse = S['SearchResultResponse'];
export type TranscodeSettingsResponse = S['TranscodeSettingsResponse'];
export type ServerTranscodeCapabilities = S['ServerTranscodeCapabilities'];
export type ItemSummary = S['ItemSummary'];
export type NextUpEpisode = S['NextUpEpisode'];
export type AlbumSummary = S['AlbumSummary'];
export type ArtistSummary = S['ArtistSummary'];
export type ArtistDetail = S['ArtistDetail'];
export type ArtistListResponse = S['ArtistListResponse'];
export type TrackListResponse = S['TrackListResponse'];
export type GenreSummary = S['GenreSummary'];
export type GenreListResponse = S['GenreListResponse'];
export type LibrarySummary = S['LibrarySummary'];
export type FilesystemBrowseResponse = S['FilesystemBrowseResponse'];
export type FilesystemEntry = S['FilesystemEntry'];
