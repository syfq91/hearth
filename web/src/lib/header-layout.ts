/**
 * Responsive modes for the primary nav in the top bar, widest first:
 * one link per library → grouped by media type → grouped, icons only.
 *
 * Which mode fits is measured from the live DOM (the last nav item versus the
 * actions cluster). This module owns only the state machine and its hysteresis,
 * so the transitions can be tested without a browser.
 */
export interface HeaderLayout {
	combined: boolean;
	compact: boolean;
	/** Width at which the ungrouped nav should be tried again. */
	minUncombinedWidth: number;
	/** Width at which labels should be tried again. */
	minCompactWidth: number;
}

export function initialHeaderLayout(): HeaderLayout {
	return {
		combined: false,
		compact: false,
		minUncombinedWidth: 0,
		minCompactWidth: 0
	};
}

/** Extra room a wider mode must gain before it is tried again. */
const HYSTERESIS = 15;
/** Conservative growth assigned when a mode is first found to overflow. */
const GROWTH = 20;

/**
 * Advance to the widest mode that fits. `overflow` is true when the painted
 * right edge of the last nav item crosses into the actions cluster.
 * Returns the same object when nothing changes, so callers can skip updates.
 */
export function nextHeaderLayout(
	state: HeaderLayout,
	width: number,
	overflow: boolean
): HeaderLayout {
	if (overflow) {
		if (!state.combined) {
			return {
				combined: true,
				compact: false,
				minUncombinedWidth: width + GROWTH,
				minCompactWidth: 0
			};
		}
		if (!state.compact) {
			return { ...state, compact: true, minCompactWidth: width + GROWTH };
		}
		// Even icons overflow: raise the bar so growing never oscillates.
		return { ...state, minCompactWidth: Math.max(state.minCompactWidth, width + GROWTH) };
	}

	if (state.compact) {
		return width >= state.minCompactWidth + HYSTERESIS ? { ...state, compact: false } : state;
	}

	if (
		state.combined &&
		state.minUncombinedWidth > 0 &&
		width >= state.minUncombinedWidth + HYSTERESIS
	) {
		return { ...state, combined: false, minCompactWidth: 0 };
	}

	return state;
}
