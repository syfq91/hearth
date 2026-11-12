import test from 'node:test';
import assert from 'node:assert';
import {
	detectBrowserCapabilities,
	clearCapabilitiesCache
} from './capabilities.ts';

test('detectBrowserCapabilities emits H.264-only profile when MediaSource only supports H.264', () => {
	clearCapabilitiesCache();

	// Mock window.MediaSource
	const origWindow = (globalThis as any).window;
	(globalThis as any).window = {
		MediaSource: {
			isTypeSupported: (type: string) => {
				const t = type.toLowerCase();
				if (t.includes('avc1') || t.includes('mp4a.40.2') || t === 'video/mp4') {
					return true;
				}
				return false;
			}
		}
	};

	try {
		const caps = detectBrowserCapabilities();
		assert.ok(caps.video_codecs && caps.video_codecs.includes('h264'), 'Should include h264');
		assert.strictEqual(caps.video_codecs?.includes('hevc'), false, 'Should not include hevc');
		assert.strictEqual(caps.video_codecs?.includes('vp9'), false, 'Should not include vp9');
		assert.strictEqual(caps.video_codecs?.includes('av1'), false, 'Should not include av1');
		assert.ok(caps.audio_codecs && caps.audio_codecs.includes('aac'), 'Should include aac');
		assert.strictEqual(caps.hls_fmp4, true, 'Should support fMP4');
	} finally {
		(globalThis as any).window = origWindow;
		clearCapabilitiesCache();
	}
});

test('detectBrowserCapabilities emits H.264+HEVC profile when MediaSource supports both', () => {
	clearCapabilitiesCache();

	const origWindow = (globalThis as any).window;
	(globalThis as any).window = {
		MediaSource: {
			isTypeSupported: (type: string) => {
				const t = type.toLowerCase();
				if (
					t.includes('avc1') ||
					t.includes('hvc1') ||
					t.includes('hev1') ||
					t.includes('mp4a.40.2') ||
					t === 'video/mp4'
				) {
					return true;
				}
				return false;
			}
		}
	};

	try {
		const caps = detectBrowserCapabilities();
		assert.ok(caps.video_codecs && caps.video_codecs.includes('h264'), 'Should include h264');
		assert.ok(caps.video_codecs && caps.video_codecs.includes('hevc'), 'Should include hevc');
		assert.ok(caps.video_profiles && caps.video_profiles.includes('main 10'), 'Should include main 10 for HEVC');
		assert.strictEqual(caps.video_codecs?.includes('vp9'), false, 'Should not include vp9');
	} finally {
		(globalThis as any).window = origWindow;
		clearCapabilitiesCache();
	}
});

test('detectBrowserCapabilities guards against missing MediaSource (e.g. iOS Safari)', () => {
	clearCapabilitiesCache();

	const origWindow = (globalThis as any).window;
	const origDocument = (globalThis as any).document;

	// No MediaSource on window
	(globalThis as any).window = {};
	(globalThis as any).document = {
		createElement: (tag: string) => {
			if (tag === 'video') {
				return {
					canPlayType: (type: string) => {
						const t = type.toLowerCase();
						if (t.includes('application/vnd.apple.mpegurl')) return 'probably';
						if (t.includes('avc1') || t.includes('mp4a.40.2') || t === 'video/mp4') return 'probably';
						return '';
					}
				};
			}
			return {};
		}
	};

	try {
		const caps = detectBrowserCapabilities();
		assert.ok(caps.video_codecs && caps.video_codecs.includes('h264'), 'Should include h264');
		assert.strictEqual(caps.video_codecs?.includes('hevc'), false, 'Should not include hevc');
		assert.strictEqual(caps.hls_fmp4, true, 'Native HLS support implies hls_fmp4');
	} finally {
		(globalThis as any).window = origWindow;
		(globalThis as any).document = origDocument;
		clearCapabilitiesCache();
	}
});

test('detectBrowserCapabilities caches result per page load', () => {
	clearCapabilitiesCache();

	let callCount = 0;
	const origWindow = (globalThis as any).window;
	(globalThis as any).window = {
		MediaSource: {
			isTypeSupported: (_type: string) => {
				callCount++;
				return true;
			}
		}
	};

	try {
		const caps1 = detectBrowserCapabilities();
		const countAfterFirst = callCount;
		assert.ok(countAfterFirst > 0, 'Should probe on first call');

		const caps2 = detectBrowserCapabilities();
		assert.strictEqual(callCount, countAfterFirst, 'Second call must use cached result without re-probing');
		assert.strictEqual(caps1, caps2, 'Cached instance should be returned');
	} finally {
		(globalThis as any).window = origWindow;
		clearCapabilitiesCache();
	}
});
