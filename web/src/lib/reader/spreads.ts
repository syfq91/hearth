/** Pure helpers for laying out comic pages into reader views. */

export interface PageDimensions {
	width: number;
	height: number;
}

/** Pages wider than this ratio are double-page spreads already. */
export const WIDE_PAGE_RATIO = 1.3;

export function isWidePage(dimensions?: PageDimensions): boolean {
	return Boolean(dimensions && dimensions.width / dimensions.height > WIDE_PAGE_RATIO);
}

export interface Spread {
	/** 1-indexed pages shown in this view. */
	pages: number[];
	/** First page of the next view, or this page when at the end. */
	next: number;
	/** First page of the previous view, or this page when at the start. */
	prev: number;
}

type Dimensions = Record<number, PageDimensions>;

/** Next view's start and whether the current view holds a second page. */
function layoutAt(page: number, pageCount: number, dimensions: Dimensions) {
	const first = Math.max(1, Math.min(page, Math.max(pageCount, 1)));
	if (first === 1) {
		return { first, hasSecond: false, next: pageCount >= 2 ? 2 : 1 };
	}
	if (isWidePage(dimensions[first])) {
		return { first, hasSecond: false, next: first + 1 <= pageCount ? first + 1 : first };
	}
	const hasSecond = first + 1 <= pageCount && !isWidePage(dimensions[first + 1]);
	const next = hasSecond
		? first + 2 <= pageCount
			? first + 2
			: first
		: first + 1 <= pageCount
			? first + 1
			: first;
	return { first, hasSecond, next };
}

/** First page of the view immediately before the one starting at `page`. */
function previousStart(page: number, pageCount: number, dimensions: Dimensions): number {
	let candidate = 1;
	let previous = 1;
	while (candidate < page) {
		const next = layoutAt(candidate, pageCount, dimensions).next;
		if (next > page || next === candidate) break;
		previous = candidate;
		candidate = next;
	}
	return previous;
}

/** The cover stands alone; the rest of the book pairs up as (2,3), (4,5)… */
export function spreadAt(page: number, pageCount: number, dimensions: Dimensions = {}): Spread {
	const { first, hasSecond, next } = layoutAt(page, pageCount, dimensions);
	return {
		pages: hasSecond ? [first, first + 1] : [first],
		next,
		prev: previousStart(first, pageCount, dimensions)
	};
}

/** Single-page view used when the reader is not in double-page mode. */
export function singleSpread(page: number, pageCount: number): Spread {
	const first = Math.max(1, Math.min(page, Math.max(pageCount, 1)));
	return {
		pages: [first],
		next: first < pageCount ? first + 1 : first,
		prev: first > 1 ? first - 1 : first
	};
}

/** Number of views in a book when laid out in double-page mode. */
export function spreadCount(pageCount: number, dimensions: Dimensions = {}): number {
	if (pageCount <= 0) return 0;
	let count = 0;
	let page = 1;
	while (page <= pageCount && count <= pageCount) {
		count += 1;
		const next = layoutAt(page, pageCount, dimensions).next;
		if (next === page) break;
		page = next;
	}
	return count;
}

/** 1-based index of the view that starts at (or contains) `page`. */
export function spreadIndex(page: number, pageCount: number, dimensions: Dimensions = {}): number {
	let index = 1;
	let candidate = 1;
	while (candidate < page && index <= pageCount) {
		const next = layoutAt(candidate, pageCount, dimensions).next;
		if (next === candidate) break;
		candidate = next;
		index += 1;
	}
	return index;
}

/** First page of the view containing `page`, used when switching modes. */
export function spreadStartFor(page: number, pageCount: number, dimensions: Dimensions = {}): number {
	let candidate = 1;
	while (candidate < page) {
		const { next } = layoutAt(candidate, pageCount, dimensions);
		if (next > page || next === candidate) break;
		candidate = next;
	}
	return candidate;
}
