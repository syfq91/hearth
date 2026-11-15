import test from 'node:test';
import assert from 'node:assert';

// Polyfill $state for raw Node test environment running without Svelte compiler transform
if (typeof (globalThis as any).$state === 'undefined') {
	(globalThis as any).$state = (v: any) => v;
}

import { SubtitleManager } from './subtitles.svelte.ts';

test('SubtitleManager applies only the user offset', () => {
	const mockVideo = {
		appendChild: () => {},
		removeChild: () => {},
		currentTime: 0,
		clientWidth: 1920,
		clientHeight: 1080
	} as unknown as HTMLVideoElement;

	const manager = new SubtitleManager({ video: mockVideo });
	assert.strictEqual(manager.userOffsetSeconds, 0);

	manager.setOffset(-1.2);
	assert.strictEqual(manager.userOffsetSeconds, -1.2);

	manager.setOffset(0.5);
	assert.strictEqual(manager.userOffsetSeconds, 0.5);
});

test('SubtitleManager loads and clears native track elements', async () => {
	const appendedTracks: any[] = [];
	const removedTracks: any[] = [];

	const mockVideo = {
		appendChild: (child: any) => {
			appendedTracks.push(child);
			child.track = { mode: 'disabled' };
		},
		removeChild: (child: any) => {
			removedTracks.push(child);
		},
		currentTime: 0
	} as unknown as HTMLVideoElement;

	// Mock document.createElement for track
	const origDocument = (globalThis as any).document;
	(globalThis as any).document = {
		createElement: (tag: string) => {
			if (tag === 'track') {
				return {
					kind: '',
					label: '',
					srclang: '',
					src: '',
					default: false,
					track: { mode: 'disabled' }
				};
			}
			return {};
		}
	};

	try {
		const manager = new SubtitleManager({ video: mockVideo });
		assert.strictEqual(manager.activeTrackId, null);

		await manager.loadTrack({
			id: 'ext:sub-1',
			url: 'http://localhost/sub.vtt',
			format: 'vtt',
			language: 'en',
			label: 'English'
		});

		assert.strictEqual(manager.activeTrackId, 'ext:sub-1');
		assert.strictEqual(manager.isAss, false);
		assert.strictEqual(appendedTracks.length, 1);
		assert.strictEqual(appendedTracks[0].track.mode, 'showing');

		await manager.clearTrack();
		assert.strictEqual(manager.activeTrackId, null);
		assert.strictEqual(removedTracks.length, 1);
	} finally {
		(globalThis as any).document = origDocument;
	}
});
