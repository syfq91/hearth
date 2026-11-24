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
import { playerUi } from './player/ui.svelte';
import {
	REPEAT_MODES,
	indexAfterRemoval,
	nextIndex,
	shuffledUpcoming,
	type RepeatMode
} from './player/queue';

export interface PlayerTrack {
	id: string;
	title: string;
	artistName: string | null;
	albumTitle: string | null;
	/** API stream path, e.g. `/api/v1/stream/audio/<id>`. */
	streamUrl: string;
	/** Artwork URL when the caller already knows the album cover. */
	artworkUrl?: string | null;
	/** Album id used to resolve the cover when `artworkUrl` is absent. */
	albumId?: string | null;
	/** Server-side favorite flag for this item. */
	favorite?: boolean;
	/** Track length in seconds, shown in the full-page queue. */
	durationSeconds?: number | null;
	/** ReplayGain tags, used to level playback volume in the audio player. */
	replayGainTrackGain?: number | null;
	replayGainTrackPeak?: number | null;
	replayGainAlbumGain?: number | null;
	replayGainAlbumPeak?: number | null;
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

/** Guards against an older token resolution overwriting a newer track's src. */
let loadGeneration = 0;

/**
 * Load a queue entry into the player. Only callers that actually start playback
 * pass `autoplay: true`; queue-only actions leave `player.playing` untouched.
 * The generation check prevents a stale token response from replacing the src
 * of a track selected after this load began.
 */
async function loadTrack(index: number, autoplay: boolean): Promise<void> {
	const generation = ++loadGeneration;
	const track = player.queue[index];
	if (!track) return;
	player.index = index;
	player.track = track;
	player.favorite = track.favorite ?? false;
	try {
		const src = await resolveSrc(track);
		if (generation !== loadGeneration) return;
		player.src = src;
		if (autoplay) player.playing = true;
	} catch (e) {
		if (generation !== loadGeneration) return;
		player.error = e instanceof Error ? e.message : String(e);
		player.playing = false;
	}
}

async function playIndex(index: number): Promise<void> {
	await loadTrack(index, true);
}

/** Jump to a track in the current queue (used by the full-page player). */
export async function playAt(index: number): Promise<boolean> {
	if (index < 0 || index >= player.queue.length) return false;
	await playIndex(index);
	return true;
}

/** Single-flight loader for the first queue entry when adding while idle. */
let preparingFirst: Promise<void> | null = null;

/**
 * Load queue[0] without touching play intent. Repeated idle actions reuse the
 * same in-flight load instead of calling playQueue() again, which used to reset
 * the queue and briefly set playing = true.
 */
function ensureQueuePrepared(): void {
	if (player.track || preparingFirst) return;
	playerUi.currentTime = 0;
	playerUi.duration = 0;
	preparingFirst = loadTrack(0, false).finally(() => {
		preparingFirst = null;
	});
}

/** Append a track to the queue without interrupting playback. */
export function addToQueue(track: PlayerTrack): void {
	if (player.queue.length === 0) {
		player.queue = [track];
		originalQueue = [track];
		player.index = 0;
		ensureQueuePrepared();
		return;
	}
	player.queue.push(track);
	originalQueue?.push(track);
}

/**
 * Insert a track right after the current one without interrupting playback.
 * With shuffle on, "next" means next in the displayed order.
 */
export function queueNext(track: PlayerTrack): void {
	if (player.queue.length === 0) {
		player.queue = [track];
		originalQueue = [track];
		player.index = 0;
		ensureQueuePrepared();
		return;
	}
	player.queue.splice(Math.min(player.index + 1, player.queue.length), 0, track);
	if (originalQueue) {
		const currentId = player.track?.id;
		const originalIndex = currentId
			? originalQueue.findIndex((item) => item.id === currentId)
			: -1;
		originalQueue.splice(
			originalIndex >= 0 ? originalIndex + 1 : originalQueue.length,
			0,
			track
		);
	}
}

/**
 * Queue a whole list without interrupting playback. `next` inserts after the
 * current track in order; `end` appends. When nothing is queued, the first
 * track is prepared paused so Play can start the list.
 */
export function addTracksToQueue(tracks: PlayerTrack[], mode: 'end' | 'next'): void {
	if (tracks.length === 0) return;
	if (player.queue.length === 0) {
		player.queue = [...tracks];
		originalQueue = [...tracks];
		player.index = 0;
		ensureQueuePrepared();
		return;
	}
	if (mode === 'end') {
		for (const track of tracks) {
			player.queue.push(track);
			originalQueue?.push(track);
		}
		return;
	}
	const insertAt = Math.min(player.index + 1, player.queue.length);
	player.queue.splice(insertAt, 0, ...tracks);
	if (originalQueue) {
		const currentId = player.track?.id;
		const originalIndex = currentId
			? originalQueue.findIndex((item) => item.id === currentId)
			: -1;
		const target = originalIndex >= 0 ? originalIndex + 1 : originalQueue.length;
		originalQueue.splice(target, 0, ...tracks);
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

/** Toggle a track's server-side favorite flag (current track by default). */
export async function toggleFavorite(track: PlayerTrack | null = player.track): Promise<void> {
	if (!track) return;
	const next = !track.favorite;
	try {
		await api.post<{ favorite: boolean }>(`/items/${track.id}/favorite`, {
			body: { favorite: next }
		});
		track.favorite = next;
		if (player.track?.id === track.id) player.favorite = next;
	} catch (e) {
		player.error = e instanceof Error ? e.message : String(e);
	}
}

/** Remove a queued track. Removing the current one advances to its successor. */
export function removeFromQueue(index: number): void {
	if (index < 0 || index >= player.queue.length) return;
	const removed = player.queue[index];
	const isCurrent = index === player.index;
	player.queue.splice(index, 1);
	if (originalQueue) {
		const originalIndex = originalQueue.findIndex((track) => track.id === removed.id);
		if (originalIndex >= 0) originalQueue.splice(originalIndex, 1);
	}
	if (player.queue.length === 0) {
		stop();
		return;
	}
	if (isCurrent) {
		// Let playIndex re-apply src and metadata for the replacement track.
		player.index = -1;
		void playAt(Math.min(index, player.queue.length - 1));
	} else {
		player.index = indexAfterRemoval(index, player.index);
	}
}

export function stop(): void {
	loadGeneration += 1;
	preparingFirst = null;
	player.track = null;
	player.src = null;
	player.playing = false;
	player.favorite = false;
	player.queue = [];
	player.index = -1;
	originalQueue = null;
	playerUi.currentTime = 0;
	playerUi.duration = 0;
}
