/** Shared auto-hide timing for immersive viewers (video and book readers). */
export const CHROME_HIDE_MS = 2500;

export interface AutoHideChromeOptions {
	/** Delay after the last activity before the chrome fades. Defaults to CHROME_HIDE_MS. */
	hideDelayMs?: number;
	/** True while a paused player or an open menu requires the chrome to stay put. */
	persist: () => boolean;
	/** Called whenever visibility changes. */
	onChange: (visible: boolean) => void;
}

export interface AutoHideChrome {
	readonly visible: boolean;
	/** Mark activity: reveal the chrome and restart the hide timer. */
	reveal(): void;
	/** Hide immediately and cancel the pending timer. */
	hide(): void;
	toggle(): void;
	/** Cancel the pending timer, e.g. on component teardown. */
	dispose(): void;
}

/**
 * One visibility state machine for every viewer that fades its controls over
 * an immersive viewport. Kept free of Svelte runes so it can be unit tested
 * with the repo's plain-Node test runner; owners mirror `visible` into `$state`.
 */
export function createAutoHideChrome(options: AutoHideChromeOptions): AutoHideChrome {
	const hideDelayMs = options.hideDelayMs ?? CHROME_HIDE_MS;
	let visible = true;
	let timer: ReturnType<typeof setTimeout> | null = null;

	function clearTimer(): void {
		if (timer !== null) clearTimeout(timer);
		timer = null;
	}

	function setVisible(next: boolean): void {
		if (visible === next) return;
		visible = next;
		options.onChange(next);
	}

	function scheduleHide(): void {
		clearTimer();
		timer = setTimeout(() => {
			timer = null;
			if (options.persist()) {
				// A menu/paused player opened while the timer was running: keep
				// re-arming instead of fading chrome out from under the user.
				scheduleHide();
				return;
			}
			setVisible(false);
		}, hideDelayMs);
	}

	function reveal(): void {
		setVisible(true);
		scheduleHide();
	}

	function hide(): void {
		clearTimer();
		setVisible(false);
	}

	function toggle(): void {
		if (visible) hide();
		else reveal();
	}

	return {
		get visible() {
			return visible;
		},
		reveal,
		hide,
		toggle,
		dispose: clearTimer
	};
}
