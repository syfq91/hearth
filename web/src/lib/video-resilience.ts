/**
 * Video player resilience utilities (T11).
 *
 * Implements:
 * 1. 503 Retry-After backoff to avoid request storms on transcode timeouts / circuit breaker cooldowns.
 * 2. Unload / mobile lifecycle stop reporting using navigator.sendBeacon with keepalive fetch fallback.
 */

/** Parse Retry-After HTTP header value in seconds, bounded between 0.5s and 10s. */
export function parseRetryAfter(
	headerValue: string | null | undefined,
	defaultSeconds = 1.0
): number {
	if (!headerValue) return defaultSeconds;
	const parsed = parseFloat(headerValue.trim());
	if (Number.isNaN(parsed) || !Number.isFinite(parsed) || parsed <= 0) {
		return defaultSeconds;
	}
	return Math.max(0.5, Math.min(10.0, parsed));
}

/**
 * Send an explicit session stop beacon on pagehide / unmount.
 *
 * Uses `navigator.sendBeacon` for mobile reliability, falling back to
 * `fetch(..., { keepalive: true })`.
 */
export function sendStopBeacon(sessionId: string, token?: string | null): boolean {
	if (!sessionId) return false;
	const queryParam = token ? `?token=${encodeURIComponent(token)}` : '';
	const url = `/api/v1/hls/${encodeURIComponent(sessionId)}/stop${queryParam}`;

	if (typeof navigator !== 'undefined' && typeof navigator.sendBeacon === 'function') {
		try {
			// sendBeacon cannot set custom headers, so query param token is passed
			const sent = navigator.sendBeacon(url);
			if (sent) return true;
		} catch {
			/* Fall through to keepalive fetch */
		}
	}

	if (typeof fetch === 'function') {
		try {
			const body = token ? JSON.stringify({ token }) : undefined;
			void fetch(url, {
				method: 'POST',
				keepalive: true,
				headers: body ? { 'Content-Type': 'application/json' } : undefined,
				body
			}).catch(() => {});
			return true;
		} catch {
			/* Best effort */
		}
	}

	return false;
}

export interface RetryOptions {
	maxAttempts?: number;
	maxDelayMs?: number;
}

/**
 * Factory for hls.js custom loader that honors 503 Retry-After headers with backoff.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function createHlsRetryLoaderClass(BaseLoader: any, options: RetryOptions = {}) {
	const maxAttempts = options.maxAttempts ?? 5;
	const maxDelayMs = options.maxDelayMs ?? 10_000;

	return class HearthHlsRetryLoader extends BaseLoader {
		private retryTimer: ReturnType<typeof setTimeout> | null = null;
		private aborted = false;

		private clearRetryTimer(): void {
			if (this.retryTimer !== null) {
				clearTimeout(this.retryTimer);
				this.retryTimer = null;
			}
		}

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		load(context: any, config: any, callbacks: any) {
			this.aborted = false;
			this.clearRetryTimer();
			const originalOnError = callbacks.onError;
			let attempts = 0;

			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const wrappedCallbacks = {
				...callbacks,
				// eslint-disable-next-line @typescript-eslint/no-explicit-any
				onError: (error: any, ctx: any, networkDetails: any, stats: any) => {
					if (this.aborted) return;
					const status =
						error?.code ??
						networkDetails?.status ??
						networkDetails?.response?.status ??
						error?.response?.status;

					if (status === 503 && attempts < maxAttempts) {
						attempts += 1;
						let retrySeconds = 1.0;
						if (networkDetails && typeof networkDetails.getResponseHeader === 'function') {
							const header = networkDetails.getResponseHeader('Retry-After');
							retrySeconds = parseRetryAfter(header, 1.0);
						} else if (networkDetails?.responseHeaders?.['retry-after']) {
							retrySeconds = parseRetryAfter(networkDetails.responseHeaders['retry-after'], 1.0);
						}

						const delayMs = Math.min(maxDelayMs, retrySeconds * 1000);
						this.clearRetryTimer();
						this.retryTimer = setTimeout(() => {
							this.retryTimer = null;
							if (this.aborted) return;
							super.load(context, config, wrappedCallbacks);
						}, delayMs);
						return;
					}

					if (typeof originalOnError === 'function') {
						originalOnError(error, ctx, networkDetails, stats);
					}
				}
			};

			super.load(context, config, wrappedCallbacks);
		}

		abort(): void {
			this.aborted = true;
			this.clearRetryTimer();
			super.abort?.();
		}

		destroy(): void {
			this.aborted = true;
			this.clearRetryTimer();
			super.destroy?.();
		}
	};
}
