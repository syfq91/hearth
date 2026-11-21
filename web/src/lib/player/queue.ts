/** Pure queue-ordering helpers for the persistent audio player. */

export type RepeatMode = 'off' | 'all' | 'one';

export const REPEAT_MODES: RepeatMode[] = ['off', 'all', 'one'];

/**
 * Shuffle only the upcoming portion of a queue, keeping already-played tracks
 * (and the current one) in place so "previous" still walks the played history.
 */
export function shuffledUpcoming<T>(
	items: readonly T[],
	index: number,
	random: () => number = Math.random
): T[] {
	const head = items.slice(0, index + 1);
	const tail = items.slice(index + 1);
	for (let i = tail.length - 1; i > 0; i--) {
		const j = Math.floor(random() * (i + 1));
		const tmp = tail[i];
		tail[i] = tail[j];
		tail[j] = tmp;
	}
	return [...head, ...tail];
}

/** Current-track index after removing a different track from the queue. */
export function indexAfterRemoval(removed: number, current: number): number {
	return removed < current ? current - 1 : current;
}

/** Next playable index, or -1 when the queue is exhausted. */
export function nextIndex(current: number, length: number, repeatAll: boolean): number {
	if (length <= 0 || current < 0) return -1;
	const next = current + 1;
	if (next < length) return next;
	return repeatAll ? 0 : -1;
}
