/**
 * Resolve artwork for a queued track.
 *
 * Track items have no artwork of their own: the album cover is the art. Some
 * callers already know that cover (album pages, album shelves); others only
 * carry `album_id` (song lists, artist top tracks), so fetch the album detail
 * lazily and cache it per album.
 */
import { api } from './api/client.ts';
import type { AlbumDetail } from './api/types.ts';
import { withMediaToken } from './media-token.svelte.ts';

export interface ArtworkSource {
	artworkUrl?: string | null;
	albumId?: string | null;
}

const albumArtwork = new Map<string, Promise<string | null>>();

function fetchAlbumArtwork(albumId: string): Promise<string | null> {
	let pending = albumArtwork.get(albumId);
	if (!pending) {
		pending = api
			.get<AlbumDetail>(`/albums/${albumId}`)
			.then((album) => album.poster?.url ?? null)
			.catch(() => null);
		albumArtwork.set(albumId, pending);
	}
	return pending;
}

/** Tokenized artwork URL for a track, or null when no artwork is available. */
export async function resolveTrackArtworkSrc(track: ArtworkSource): Promise<string | null> {
	const raw =
		track.artworkUrl || (track.albumId ? await fetchAlbumArtwork(track.albumId) : null);
	if (!raw) return null;
	try {
		return await withMediaToken(raw);
	} catch {
		return null;
	}
}
