/**
 * D-pad / arrow-key spatial navigation for 10-foot TV mode.
 *
 * Attached to the app shell: when TV mode is on, Arrow keys move focus to the
 * nearest focusable element in that direction (geometry-based, like a TV
 * browser's own spatial navigation), scroll it into view and keep the focus
 * ring visible. Native behaviour is left alone inside form controls and when
 * TV mode is off, so the desktop keyboard experience is unchanged.
 */
import type { ActionReturn } from 'svelte/action';
import { tv } from '../tv.svelte';

const FOCUSABLE =
	'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

type Direction = 'up' | 'down' | 'left' | 'right';

const DIRECTIONS: Record<string, Direction> = {
	ArrowUp: 'up',
	ArrowDown: 'down',
	ArrowLeft: 'left',
	ArrowRight: 'right'
};

/** Minimum axis travel before a candidate counts as "in that direction". */
const MIN_TRAVEL_PX = 4;
/** Weight of the perpendicular offset: nearby-but-aligned wins over far-away. */
const PERPENDICULAR_WEIGHT = 2;

function center(element: Element): { x: number; y: number } {
	const rect = element.getBoundingClientRect();
	return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
}

function isVisible(element: HTMLElement): boolean {
	return element.getClientRects().length > 0;
}

function candidates(container: HTMLElement): HTMLElement[] {
	return [...container.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(isVisible);
}

/**
 * Score a candidate for movement in `direction`; negative when the candidate
 * is not in that direction at all.
 */
function score(
	from: { x: number; y: number },
	to: { x: number; y: number },
	direction: Direction
): number {
	switch (direction) {
		case 'left': {
			const dx = from.x - to.x;
			const dy = Math.abs(from.y - to.y);
			return dx > MIN_TRAVEL_PX ? dx + dy * PERPENDICULAR_WEIGHT : -1;
		}
		case 'right': {
			const dx = to.x - from.x;
			const dy = Math.abs(from.y - to.y);
			return dx > MIN_TRAVEL_PX ? dx + dy * PERPENDICULAR_WEIGHT : -1;
		}
		case 'up': {
			const dy = from.y - to.y;
			const dx = Math.abs(from.x - to.x);
			return dy > MIN_TRAVEL_PX ? dy + dx * PERPENDICULAR_WEIGHT : -1;
		}
		case 'down': {
			const dy = to.y - from.y;
			const dx = Math.abs(from.x - to.x);
			return dy > MIN_TRAVEL_PX ? dy + dx * PERPENDICULAR_WEIGHT : -1;
		}
	}
}

function moveFocus(container: HTMLElement, direction: Direction): void {
	const options = candidates(container);
	if (options.length === 0) return;

	const active = document.activeElement as HTMLElement | null;
	if (!active || active === document.body || !container.contains(active)) {
		// Nothing focused yet: enter the UI from the top-left-most element.
		const first = options.reduce((best, element) => {
			const point = center(element);
			const bestPoint = center(best);
			return point.y < bestPoint.y || (point.y === bestPoint.y && point.x < bestPoint.x)
				? element
				: best;
		});
		first.focus({ preventScroll: true });
		first.scrollIntoView({ block: 'nearest', inline: 'nearest' });
		return;
	}

	const from = center(active);
	let best: HTMLElement | null = null;
	let bestScore = Number.POSITIVE_INFINITY;
	for (const option of options) {
		if (option === active) continue;
		const candidateScore = score(from, center(option), direction);
		if (candidateScore >= 0 && candidateScore < bestScore) {
			bestScore = candidateScore;
			best = option;
		}
	}
	if (!best) return;
	best.focus({ preventScroll: true });
	best.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

/** Apply to the app shell; active only while TV mode is on. */
export function spatialFocus(node: HTMLElement): ActionReturn {
	function handleKeydown(event: KeyboardEvent): void {
		if (!tv.on) return;
		const direction = DIRECTIONS[event.key];
		if (!direction) return;
		const target = event.target as HTMLElement | null;
		// Form controls keep their native arrow-key behaviour.
		if (target?.closest('input, select, textarea, [contenteditable="true"]')) return;
		event.preventDefault();
		moveFocus(node, direction);
	}

	node.addEventListener('keydown', handleKeydown);
	return {
		destroy() {
			node.removeEventListener('keydown', handleKeydown);
		}
	};
}
