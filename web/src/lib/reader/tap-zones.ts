/** Shared pointer-gesture helpers for the book readers. */

export type TapZone = 'prev' | 'toggle' | 'next';

/**
 * Split a reading surface into three vertical thirds. Tapping the left third
 * goes back, the right third goes forward, and the middle third is reserved
 * for toggling the header.
 */
export function tapZoneAt(clientX: number, rect: DOMRect): TapZone {
	const ratio = (clientX - rect.left) / Math.max(rect.width, 1);
	if (ratio < 1 / 3) return 'prev';
	if (ratio > 2 / 3) return 'next';
	return 'toggle';
}

export interface Point {
	x: number;
	y: number;
}

/** Pointer movement under this distance (px) still counts as a tap. */
export function isTap(start: Point, end: Point, threshold = 12): boolean {
	return Math.abs(end.x - start.x) <= threshold && Math.abs(end.y - start.y) <= threshold;
}

export type SwipeDirection = 'left' | 'right';

/**
 * Detect a mostly-horizontal swipe. A left swipe (finger moves left) advances
 * the page; a right swipe goes back. Short or mostly-vertical drags are not
 * swipes so they can still be taps.
 */
export function horizontalSwipe(start: Point, end: Point, threshold = 50): SwipeDirection | null {
	const dx = end.x - start.x;
	const dy = end.y - start.y;
	if (Math.abs(dx) < threshold || Math.abs(dx) <= Math.abs(dy)) return null;
	return dx < 0 ? 'left' : 'right';
}
