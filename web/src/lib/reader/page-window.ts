/** Inclusive page range kept rendered around the current page. */
export function pageWindow(
	current: number,
	total: number,
	before = 1,
	after = 2
): { first: number; last: number } {
	if (total <= 0) return { first: 1, last: 0 };
	const center = Math.min(Math.max(Math.trunc(current) || 1, 1), total);
	return {
		first: Math.max(1, center - before),
		last: Math.min(total, center + after)
	};
}
