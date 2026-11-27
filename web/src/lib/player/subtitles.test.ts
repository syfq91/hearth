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

function mockTrackEnvironment() {
	const appended: any[] = [];
	const removed: any[] = [];
	const video = {
		appendChild: (child: any) => {
			appended.push(child);
			child.track.mode = 'disabled';
		},
		removeChild: (child: any) => {
			removed.push(child);
		},
		currentTime: 0
	} as unknown as HTMLVideoElement;
	const origDocument = (globalThis as any).document;
	(globalThis as any).document = {
		createElement: () => ({
			kind: '',
			label: '',
			srclang: '',
			src: '',
			default: false,
			track: { mode: 'disabled' }
		})
	};
	return {
		video,
		appended,
		removed,
		restore: () => {
			(globalThis as any).document = origDocument;
		}
	};
}

test('overlapping loads mount only the newest track', async () => {
	const env = mockTrackEnvironment();
	try {
		const manager = new SubtitleManager({ video: env.video });
		const first = manager.loadTrack({ id: 'ext:a', url: 'a.vtt', format: 'vtt' });
		const second = manager.loadTrack({ id: 'ext:b', url: 'b.vtt', format: 'vtt' });
		await Promise.all([first, second]);

		assert.strictEqual(manager.activeTrackId, 'ext:b');
		assert.strictEqual(env.appended.length, 1);
		assert.strictEqual(env.appended[0].src, 'b.vtt');
	} finally {
		env.restore();
	}
});

test('native tracks carry the sync offset and container start offset', async () => {
	const env = mockTrackEnvironment();
	try {
		const manager = new SubtitleManager({ video: env.video });
		manager.setMediaStartSeconds(5);
		manager.setOffset(-0.5);
		await manager.loadTrack({ id: 'ext:a', url: '/sub.vtt?token=x', format: 'vtt' });
		assert.strictEqual(env.appended[0].src, '/sub.vtt?token=x&shift_ms=4500');
	} finally {
		env.restore();
	}
});

test('changing the offset remounts a native track', async () => {
	const env = mockTrackEnvironment();
	try {
		const manager = new SubtitleManager({ video: env.video });
		await manager.loadTrack({ id: 'ext:a', url: '/sub.vtt', format: 'vtt' });
		manager.setOffset(1);
		await new Promise((resolve) => setTimeout(resolve, 250));

		assert.strictEqual(env.appended.length, 2);
		assert.strictEqual(env.appended[1].src, '/sub.vtt?shift_ms=1000');
	} finally {
		env.restore();
	}
});

test('changing the offset shifts loaded native cues in-memory without remounting', async () => {
	const cues = [
		{ startTime: 10, endTime: 12 },
		{ startTime: 0.5, endTime: 2.0 }
	];
	const env = mockTrackEnvironment();
	const origCreate = (globalThis as any).document.createElement;
	(globalThis as any).document.createElement = () => {
		const el = origCreate();
		el.track = { mode: 'disabled', cues };
		return el;
	};
	try {
		const manager = new SubtitleManager({ video: env.video });
		await manager.loadTrack({ id: 'ext:a', url: '/sub.vtt', format: 'vtt' });
		manager.setOffset(1.5);
		assert.strictEqual(env.appended.length, 1);
		assert.strictEqual(cues[0].startTime, 11.5);
		assert.strictEqual(cues[0].endTime, 13.5);
		assert.strictEqual(cues[1].startTime, 2.0);
		assert.strictEqual(cues[1].endTime, 3.5);

		manager.setOffset(-1.0);
		assert.strictEqual(cues[0].startTime, 9.0);
		assert.strictEqual(cues[0].endTime, 11.0);
		assert.strictEqual(cues[1].startTime, 0);
		assert.strictEqual(cues[1].endTime, 1.0);
	} finally {
		env.restore();
	}
});

test('clearing during a pending load mounts nothing', async () => {
	const env = mockTrackEnvironment();
	try {
		const manager = new SubtitleManager({ video: env.video });
		const load = manager.loadTrack({ id: 'ext:a', url: 'a.vtt', format: 'vtt' });
		const cleared = manager.clearTrack();
		await Promise.all([load, cleared]);

		assert.strictEqual(manager.activeTrackId, null);
		assert.strictEqual(env.appended.length, 0);
	} finally {
		env.restore();
	}
});

test('container-aligned embedded tracks do not double-apply mediaStartSeconds', async () => {
	const env = mockTrackEnvironment();
	try {
		const manager = new SubtitleManager({ video: env.video });
		manager.setMediaStartSeconds(5);
		manager.setOffset(-0.5);
		await manager.loadTrack({
			id: 'emb:0',
			url: '/api/v1/media/f1/subtitles/0.vtt?token=x',
			format: 'vtt',
			isContainerAligned: true
		});
		assert.strictEqual(env.appended[0].src, '/api/v1/media/f1/subtitles/0.vtt?token=x&shift_ms=-500');
	} finally {
		env.restore();
	}
});
