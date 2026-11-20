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
import { api } from './api/client';
import { REPEAT_MODES, nextIndex, shuffledUpcoming, type RepeatMode } from './player/queue';

export interface PlayerTrack {
	id: string;
	title: string;
	artistName: string | null;
	albumTitle: string | null;
	/** API stream path, e.g. `/api/v1/stream/audio/<id>`. */
	streamUrl: string;
	/** Artwork URL (optional; also needs a media token). */
	artworkUrl?: string | null;
	/** Server-side favorite flag for this item. */
	favorite?: boolean;
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
	/** Favorite flag of the current track. */
	favorite: boolean;
	repeat: RepeatMode;
	shuffle: boolean;
}>({
	track: null,
	src: null,
	playing: false,
	error: null,
	queue: [],
	index: -1,
	favorite: false,
	repeat: 'off',
	shuffle: false
});

/** Queue order before shuffling, restored when shuffle is switched off. */
let originalQueue: PlayerTrack[] | null = null;

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
	originalQueue = [...tracks];
	player.queue = player.shuffle ? shuffledUpcoming(tracks, index) : [...tracks];
	player.error = null;
	await playIndex(index);
}

async function playIndex(index: number): Promise<void> {
	const track = player.queue[index];
	if (!track) return;
	player.index = index;
	player.track = track;
	player.favorite = track.favorite ?? false;
	try {
		player.src = await resolveSrc(track);
		player.playing = true;
	} catch (e) {
		player.error = e instanceof Error ? e.message : String(e);
		player.playing = false;
	}
}

/** Advance to the next queued track; wraps when repeat-all is enabled. */
export async function playNext(): Promise<boolean> {
	const next = nextIndex(player.index, player.queue.length, player.repeat === 'all');
	if (next < 0) {
		player.playing = false;
		return false;
	}
	await playIndex(next);
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

/** Cycle repeat off → all → one → off. */
export function cycleRepeat(): RepeatMode {
	const position = REPEAT_MODES.indexOf(player.repeat);
	player.repeat = REPEAT_MODES[(position + 1) % REPEAT_MODES.length];
	return player.repeat;
}

/** Toggle shuffle, preserving (and restoring) the original queue order. */
export function toggleShuffle(): boolean {
	player.shuffle = !player.shuffle;
	if (player.shuffle) {
		originalQueue = [...player.queue];
		player.queue = shuffledUpcoming(player.queue, player.index);
	} else if (originalQueue) {
		const currentId = player.track?.id;
		player.queue = originalQueue;
		player.index = currentId
			? Math.max(0, player.queue.findIndex((track) => track.id === currentId))
			: -1;
		originalQueue = null;
	}
	return player.shuffle;
}

/** Toggle the current track's server-side favorite flag. */
export async function toggleFavorite(): Promise<void> {
	const track = player.track;
	if (!track) return;
	const next = !player.favorite;
	try {
		await api.post<{ favorite: boolean }>(`/items/${track.id}/favorite`, {
			body: { favorite: next }
		});
		if (player.track?.id === track.id) {
			player.favorite = next;
			track.favorite = next;
		}
	} catch (e) {
		player.error = e instanceof Error ? e.message : String(e);
	}
}

export function stop(): void {
	player.track = null;
	player.src = null;
	player.playing = false;
	player.favorite = false;
	player.queue = [];
	player.index = -1;
	originalQueue = null;
}
