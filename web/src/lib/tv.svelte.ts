/**
 * 10-foot TV mode (Svelte 5 runes).
 *
 * TV mode scales the whole UI up (rem-based type grows via `html.tv`) and
 * turns on D-pad spatial navigation (see `actions/spatial-focus.ts`).
 *
 * Activation is remembered per browser: an explicit user choice in
 * `localStorage` wins; otherwise known TV user agents (Tizen, webOS,
 * Android TV, …) enable it automatically. State lives in one object so
 * components read it like any other rune store.
 */
const STORAGE_KEY = 'hearth.tv-mode';

/** Common smart-TV browser user agents. */
const TV_USER_AGENT =
	/SmartTV|Smart-TV|Tizen|Web0S|WebOS|HbbTV|Android TV|AppleTV|Roku|NetCast|Viera|PhilipsTV/i;

function detect(): boolean {
	try {
		const stored = globalThis.localStorage?.getItem(STORAGE_KEY);
		if (stored !== null) return stored === '1';
	} catch {
		/* private mode or storage disabled: fall through to the UA check */
	}
	try {
		return TV_USER_AGENT.test(globalThis.navigator?.userAgent ?? '');
	} catch {
		return false;
	}
}

export const tv = $state({ on: detect() });

/** Toggle TV mode and remember the explicit choice. */
export function setTvMode(on: boolean): void {
	tv.on = on;
	try {
		globalThis.localStorage?.setItem(STORAGE_KEY, on ? '1' : '0');
	} catch {
		/* storage disabled: the mode still applies for this page load */
	}
}
