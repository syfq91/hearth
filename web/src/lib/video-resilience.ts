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
 * Configure Video.js VHS XHR to intercept 503 responses, honor Retry-After, and back off.
 */
export function installVhsRetryInterceptor(
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	videojsInstance: any,
	options: RetryOptions = {}
): void {
	if (!videojsInstance?.Vhs?.xhr) return;
	const originalXhr = videojsInstance.Vhs.xhr;
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	if ((originalXhr as any).__hearthRetryInstalled) return;

	const maxAttempts = options.maxAttempts ?? 5;
	const maxDelayMs = options.maxDelayMs ?? 10_000;

	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	function retryableXhr(xhrOptions: any, callback: (error: any, response: any) => void) {
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		let currentRequest: any = null;
		let isAborted = false;
		let timeoutId: ReturnType<typeof setTimeout> | null = null;
		let attempts = 0;
		const eventListeners: Record<string, Function[]> = {};

		function attachListeners(target: any) {
			if (!target || typeof target.addEventListener !== 'function') return;
			for (const [event, fns] of Object.entries(eventListeners)) {
				for (const fn of fns) {
					try {
						target.addEventListener(event, fn);
					} catch {
						/* Ignore listener attachment errors */
					}
				}
			}
		}

		function execute() {
			if (isAborted) return;
			attempts += 1;

			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			currentRequest = originalXhr(xhrOptions, (error: any, response: any) => {
				if (isAborted) return;
				const statusCode =
					response?.statusCode ?? response?.rawRequest?.status ?? error?.statusCode;

				if (statusCode === 503 && attempts < maxAttempts) {
					const rawHeader =
						response?.headers?.['retry-after'] ??
						response?.rawRequest?.getResponseHeader?.('Retry-After');
					const retrySeconds = parseRetryAfter(rawHeader, 1.0);
					// Exponential backoff factor on repeated retries
					const backoffFactor = Math.pow(1.2, attempts - 1);
					const delayMs = Math.min(maxDelayMs, Math.round(retrySeconds * 1000 * backoffFactor));

					timeoutId = setTimeout(() => {
						timeoutId = null;
						execute();
					}, delayMs);
					return;
				}

				callback(error, response);
			});

			attachListeners(currentRequest);
		}

		execute();

		// Return proxy object compatible with Video.js VHS XHR caller expectations
		const proxy: any = {
			abort() {
				isAborted = true;
				if (timeoutId) {
					clearTimeout(timeoutId);
					timeoutId = null;
				}
				if (currentRequest && typeof currentRequest.abort === 'function') {
					try {
						currentRequest.abort();
					} catch {
						/* Ignore */
					}
				}
			},
			addEventListener(event: string, fn: Function) {
				eventListeners[event] = eventListeners[event] || [];
				eventListeners[event].push(fn);
				if (currentRequest && typeof currentRequest.addEventListener === 'function') {
					currentRequest.addEventListener(event, fn);
				}
			},
			removeEventListener(event: string, fn: Function) {
				if (eventListeners[event]) {
					eventListeners[event] = eventListeners[event].filter((f) => f !== fn);
				}
				if (currentRequest && typeof currentRequest.removeEventListener === 'function') {
					currentRequest.removeEventListener(event, fn);
				}
			}
		};

		return new Proxy(proxy, {
			get(target, prop) {
				if (prop in target) return target[prop];
				if (currentRequest && prop in currentRequest) {
					const val = currentRequest[prop];
					return typeof val === 'function' ? val.bind(currentRequest) : val;
				}
				return undefined;
			}
		});
	}

	Object.assign(retryableXhr, originalXhr);
	// eslint-disable-next-line @typescript-eslint/no-explicit-any
	(retryableXhr as any).__hearthRetryInstalled = true;
	videojsInstance.Vhs.xhr = retryableXhr;
}

/**
 * Factory for hls.js custom loader that honors 503 Retry-After headers with backoff.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function createHlsRetryLoaderClass(BaseLoader: any, options: RetryOptions = {}) {
	const maxAttempts = options.maxAttempts ?? 5;
	const maxDelayMs = options.maxDelayMs ?? 10_000;

	return class HearthHlsRetryLoader extends BaseLoader {
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		load(context: any, config: any, callbacks: any) {
			const originalOnError = callbacks.onError;
			let attempts = 0;

			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const wrappedCallbacks = {
				...callbacks,
				// eslint-disable-next-line @typescript-eslint/no-explicit-any
				onError: (error: any, ctx: any, networkDetails: any, stats: any) => {
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
						setTimeout(() => {
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
	};
}
