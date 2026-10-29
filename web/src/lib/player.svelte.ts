/**
 * Persistent audio player state.
 *
 * The single `<audio>` element lives in the root layout so playback continues
 * across navigation; this module holds its state as runes. Track stream URLs
 * are API paths that need a media token (audio elements cannot send headers),
 * so the token is appended when a track is queued.
 *
 * Playback is a queue: `playQueue` starts a list at an index and `next`/
 * `previous` walk it. The Media Session API (metadata + action handlers) is
 * wired up in `AudioBar`, which owns the element.
 */
import { withMediaToken } from './media-token.svelte';

export interface PlayerTrack {
	id: string;
	title: string;
	artistName: string | null;
	albumTitle: string | null;
	/** API stream path, e.g. `/api/v1/stream/audio/<id>`. */
	streamUrl: string;
	/** Artwork URL (optional; also needs a media token). */
	artworkUrl?: string | null;
}

export const player = $state<{
	track: PlayerTrack | null;
	/** Resolved URL with the media token, set when a track is queued. */
	src: string | null;
	playing: boolean;
	error: string | null;
	/** Ordered queue; empty when nothing was queued as a list. */
	queue: PlayerTrack[];
	/** Position of `track` in `queue`, or -1. */
	index: number;
}>({
	track: null,
	src: null,
	playing: false,
	error: null,
	queue: [],
	index: -1
});

/** Resolved stream URLs for the next-track prewarm (token lives 6h server-side). */
const resolved = new Map<string, string>();

async function resolveSrc(track: PlayerTrack): Promise<string> {
	const cached = resolved.get(track.id);
	if (cached) return cached;
	const src = await withMediaToken(track.streamUrl);
	resolved.set(track.id, src);
	return src;
}

/** Queue a single track (replaces the queue with just that track). */
export async function playTrack(track: PlayerTrack): Promise<void> {
	await playQueue([track], 0);
}

/** Start `tracks` at `startIndex`, replacing the current queue. */
export async function playQueue(tracks: PlayerTrack[], startIndex = 0): Promise<void> {
	if (tracks.length === 0) return;
	const index = Math.min(Math.max(0, startIndex), tracks.length - 1);
	player.queue = tracks;
	player.error = null;
	await playIndex(index);
}

async function playIndex(index: number): Promise<void> {
	const track = player.queue[index];
	if (!track) return;
	player.index = index;
	player.track = track;
	try {
		player.src = await resolveSrc(track);
		player.playing = true;
	} catch (e) {
		player.error = e instanceof Error ? e.message : String(e);
		player.playing = false;
	}
}

/** Advance to the next queued track; returns false at the end of the queue. */
export async function playNext(): Promise<boolean> {
	if (player.index < 0 || player.index + 1 >= player.queue.length) {
		player.playing = false;
		return false;
	}
	await playIndex(player.index + 1);
	return true;
}

/** Step back to the previous track (restarts the current one at the start). */
export async function playPrevious(): Promise<boolean> {
	if (player.index < 0) return false;
	if (player.index === 0) {
		// No previous track: restart the current one from the top.
		const track = player.queue[0];
		if (!track) return false;
		player.src = null;
		await playIndex(0);
		return true;
	}
	await playIndex(player.index - 1);
	return true;
}

/** URL of the track after the current one, for the gapless prewarm. */
export async function nextTrackSrc(): Promise<string | null> {
	const next = player.queue[player.index + 1];
	if (!next) return null;
	return resolveSrc(next);
}

export function togglePlay(): void {
	player.playing = !player.playing;
}

export function stop(): void {
	player.track = null;
	player.src = null;
	player.playing = false;
	player.queue = [];
	player.index = -1;
}
