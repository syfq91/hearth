import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';

import { api, clearApiCache, setAccessToken } from './client.ts';

interface FetchCall {
	url: string;
	init?: RequestInit;
}

function setup(t: TestContext, handler: (call: FetchCall) => Response): FetchCall[] {
	const calls: FetchCall[] = [];
	const originalWindow = (globalThis as { window?: unknown }).window;
	const originalFetch = globalThis.fetch;

	(globalThis as { window?: unknown }).window = { location: { origin: 'http://localhost' } };
	globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
		const call = { url: String(input), init };
		calls.push(call);
		return handler(call);
	};
	clearApiCache();
	setAccessToken(null);

	t.after(() => {
		(globalThis as { window?: unknown }).window = originalWindow;
		globalThis.fetch = originalFetch;
		clearApiCache();
	});
	return calls;
}

function jsonResponse(body: unknown, headers: Record<string, string> = {}): Response {
	return new Response(JSON.stringify(body), {
		status: 200,
		headers: { 'content-type': 'application/json', ...headers }
	});
}

test('sequential GETs reuse the cached response', async (t) => {
	const calls = setup(t, () => jsonResponse({ value: 1 }));

	const first = await api.get<{ value: number }>('/items');
	const second = await api.get<{ value: number }>('/items');

	assert.deepEqual(first, second);
	assert.equal(calls.length, 1);
});

test('concurrent identical GETs share one in-flight request', async (t) => {
	const calls = setup(t, () => jsonResponse({ value: 2 }));

	const [first, second] = await Promise.all([
		api.get<{ value: number }>('/items'),
		api.get<{ value: number }>('/items')
	]);

	assert.deepEqual(first, { value: 2 });
	assert.deepEqual(second, { value: 2 });
	assert.equal(calls.length, 1);
});

test('cache: false bypasses the GET cache', async (t) => {
	const calls = setup(t, () => jsonResponse({ value: 3 }));

	await api.get('/items', { cache: false });
	await api.get('/items', { cache: false });

	assert.equal(calls.length, 2);
});

test('changing the access token drops cached responses', async (t) => {
	const calls = setup(t, () => jsonResponse({ value: 4 }));

	await api.get('/items');
	setAccessToken('new-token');
	await api.get('/items');

	assert.equal(calls.length, 2);
	const headers = calls[1].init?.headers as Record<string, string> | undefined;
	assert.equal(headers?.['Authorization'], 'Bearer new-token');
});
