import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolveTrackArtworkSrc } from './music-artwork.ts';

interface WindowStub {
	location: { origin: string };
}

function installFetchStub(): string[] {
	const calls: string[] = [];
	(globalThis as unknown as { window: WindowStub }).window = {
		location: { origin: 'http://localhost' }
	};
	globalThis.fetch = async (input: RequestInfo | URL) => {
		const url = String(input);
		calls.push(url);
		if (url.includes('/auth/media-tokens')) {
			return new Response(
				JSON.stringify({ token: 'media-token', expires_at: '2999-01-01T00:00:00Z' }),
				{ status: 200, headers: { 'content-type': 'application/json' } }
			);
		}
		if (url.includes('/albums/alb-1')) {
			return new Response(
				JSON.stringify({ id: 'alb-1', title: 'Album', poster: { url: '/api/v1/images/cover-1' } }),
				{ status: 200, headers: { 'content-type': 'application/json' } }
			);
		}
		throw new Error(`unexpected fetch: ${url}`);
	};
	return calls;
}

test('a caller-supplied cover is used as-is', async () => {
	const calls = installFetchStub();
	const src = await resolveTrackArtworkSrc({ artworkUrl: '/api/v1/images/cover-direct' });
	assert.equal(src, '/api/v1/images/cover-direct?token=media-token');
	assert.equal(calls.some((url) => url.includes('/albums/')), false);
});

test('a track without a cover falls back to its album', async () => {
	const calls = installFetchStub();
	const src = await resolveTrackArtworkSrc({ albumId: 'alb-1' });
	assert.equal(src, '/api/v1/images/cover-1?token=media-token');
	assert.equal(calls.some((url) => url.includes('/albums/alb-1')), true);
});
