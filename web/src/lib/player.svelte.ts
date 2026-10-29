/**
 * Persistent audio player state.
 *
 * The single `<audio>` element lives in the root layout so playback continues
 * across navigation; this module holds its state as runes. Track stream URLs
 * are API paths that need a media token (audio elements cannot send headers),
 * so the token is appended when a track is queued.
 */
import { withMediaToken } from './media-token.svelte';

export interface PlayerTrack {
	id: string;
	title: string;
	artistName: string | null;
	albumTitle: string | null;
	/** API stream path, e.g. `/api/v1/stream/audio/<id>`. */
	streamUrl: string;
}

export const player = $state<{
	track: PlayerTrack | null;
	/** Resolved URL with the media token, set when a track is queued. */
	src: string | null;
	playing: boolean;
	error: string | null;
}>({
	track: null,
	src: null,
	playing: false,
	error: null
});

/** Queue a track (optionally as part of a list for future queue support). */
export async function playTrack(track: PlayerTrack): Promise<void> {
	player.track = track;
	player.error = null;
	player.src = await withMediaToken(track.streamUrl);
	player.playing = true;
}

export function togglePlay(): void {
	player.playing = !player.playing;
}

export function stop(): void {
	player.track = null;
	player.src = null;
	player.playing = false;
}
