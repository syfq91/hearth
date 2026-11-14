import test from 'node:test';
import assert from 'node:assert';
import {
	parseRetryAfter,
	sendStopBeacon,
	installVhsRetryInterceptor,
	createHlsRetryLoaderClass
} from './video-resilience.ts';

test('parseRetryAfter parses valid seconds and handles boundaries', () => {
	assert.strictEqual(parseRetryAfter('1'), 1.0);
	assert.strictEqual(parseRetryAfter('2.5'), 2.5);
	assert.strictEqual(parseRetryAfter('10'), 10.0);
	// Clamped to 10 max
	assert.strictEqual(parseRetryAfter('60'), 10.0);
	// Clamped to 0.5 min
	assert.strictEqual(parseRetryAfter('0.1'), 0.5);
	// Fallback to default on invalid or missing
	assert.strictEqual(parseRetryAfter(null), 1.0);
	assert.strictEqual(parseRetryAfter(undefined), 1.0);
	assert.strictEqual(parseRetryAfter('invalid', 2.0), 2.0);
	assert.strictEqual(parseRetryAfter('-5', 1.5), 1.5);
});

test('sendStopBeacon sends beacon with query token when navigator.sendBeacon is available', () => {
	const origDesc = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
	let beaconUrl: string | null = null;

	Object.defineProperty(globalThis, 'navigator', {
		value: {
			sendBeacon: (url: string) => {
				beaconUrl = url;
				return true;
			}
		},
		configurable: true,
		writable: true
	});

	try {
		const result = sendStopBeacon('session_abc', 'media_token_123');
		assert.strictEqual(result, true);
		assert.strictEqual(
			beaconUrl,
			'/api/v1/hls/session_abc/stop?token=media_token_123'
		);
	} finally {
		if (origDesc) {
			Object.defineProperty(globalThis, 'navigator', origDesc);
		}
	}
});

test('sendStopBeacon falls back to keepalive fetch if sendBeacon returns false', async () => {
	const origDesc = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
	const originalFetch = (globalThis as any).fetch;
	let fetchUrl: string | null = null;
	let fetchInit: any = null;

	Object.defineProperty(globalThis, 'navigator', {
		value: {
			sendBeacon: () => false
		},
		configurable: true,
		writable: true
	});
	(globalThis as any).fetch = async (url: string, init: any) => {
		fetchUrl = url;
		fetchInit = init;
		return { ok: true };
	};

	try {
		const result = sendStopBeacon('session_fallback', 'token_fallback');
		assert.strictEqual(result, true);
		assert.strictEqual(
			fetchUrl,
			'/api/v1/hls/session_fallback/stop?token=token_fallback'
		);
		assert.strictEqual(fetchInit.method, 'POST');
		assert.strictEqual(fetchInit.keepalive, true);
	} finally {
		if (origDesc) {
			Object.defineProperty(globalThis, 'navigator', origDesc);
		}
		(globalThis as any).fetch = originalFetch;
	}
});

test('installVhsRetryInterceptor intercepts 503 and retries with backoff', async () => {
	let attempts = 0;
	const mockVjs = {
		Vhs: {
			xhr: (options: any, callback: Function) => {
				attempts += 1;
				if (attempts === 1) {
					// First attempt: return 503 with Retry-After: 0.5
					callback(null, {
						statusCode: 503,
						headers: { 'retry-after': '0.5' },
						rawRequest: { status: 503 }
					});
				} else {
					// Second attempt: succeed with 200
					callback(null, {
						statusCode: 200,
						body: new Uint8Array([1, 2, 3])
					});
				}
				return {
					abort: () => {},
					addEventListener: () => {},
					removeEventListener: () => {}
				};
			}
		}
	};

	installVhsRetryInterceptor(mockVjs, { maxAttempts: 3, maxDelayMs: 200 });

	let finalResponse: any = null;
	await new Promise<void>((resolve) => {
		mockVjs.Vhs.xhr({ uri: '/seg_001.m4s' }, (error: any, response: any) => {
			finalResponse = response;
			resolve();
		});
	});

	assert.strictEqual(attempts, 2, 'Should have retried once after 503');
	assert.strictEqual(finalResponse.statusCode, 200);
});

test('createHlsRetryLoaderClass intercepts 503 in hls.js loader and retries before error', async () => {
	let loadCalls = 0;

	class MockBaseLoader {
		load(context: any, config: any, callbacks: any) {
			loadCalls += 1;
			if (loadCalls === 1) {
				// First load fails with 503
				callbacks.onError(
					{ code: 503 },
					context,
					{
						status: 503,
						getResponseHeader: (h: string) => (h === 'Retry-After' ? '0.5' : null)
					},
					{}
				);
			} else {
				// Second load succeeds
				callbacks.onSuccess({ data: 'mock_segment_data' }, {}, context, {});
			}
		}
		destroy() {}
		abort() {}
	}

	const RetryLoader = createHlsRetryLoaderClass(MockBaseLoader, {
		maxAttempts: 3,
		maxDelayMs: 100
	});
	const loaderInstance = new RetryLoader();

	let successData: any = null;
	let errorCalled = false;

	await new Promise<void>((resolve) => {
		loaderInstance.load(
			{ url: '/seg_002.m4s' },
			{},
			{
				onSuccess: (res: any) => {
					successData = res.data;
					resolve();
				},
				onError: () => {
					errorCalled = true;
					resolve();
				}
			}
		);
	});

	assert.strictEqual(loadCalls, 2, 'Hls retry loader should have re-attempted load');
	assert.strictEqual(successData, 'mock_segment_data');
	assert.strictEqual(errorCalled, false, 'onError should not have fired on retryable 503');
});
