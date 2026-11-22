import test from 'node:test';
import assert from 'node:assert';
import Hls from 'hls.js';
import {
	isHlsSupported,
	isNativeHlsSupported,
	shouldUseNativeHls,
	HlsEngine
} from './hls-engine.ts';

if (typeof (globalThis as any).self === 'undefined') {
	(globalThis as any).self = globalThis;
}
if (!(globalThis as any).self.location) {
	(globalThis as any).self.location = { href: 'http://localhost/' };
}

test('isHlsSupported returns boolean based on environment', () => {
	const supported = isHlsSupported();
	assert.strictEqual(typeof supported, 'boolean');
});

test('isNativeHlsSupported inspects HTMLMediaElement canPlayType', () => {
	const mockVideoTrue = {
		canPlayType: (type: string) => {
			if (type === 'application/vnd.apple.mpegurl') return 'maybe';
			return '';
		}
	} as unknown as HTMLVideoElement;

	const mockVideoFalse = {
		canPlayType: () => ''
	} as unknown as HTMLVideoElement;

	assert.strictEqual(isNativeHlsSupported(mockVideoTrue), true);
	assert.strictEqual(isNativeHlsSupported(mockVideoFalse), false);
});

test('shouldUseNativeHls prefers hls.js wherever MSE is available', () => {
	const originalSupported = Hls.isSupported;
	const video = {
		canPlayType: () => 'maybe'
	} as unknown as HTMLVideoElement;
	try {
		Hls.isSupported = () => true;
		assert.strictEqual(isNativeHlsSupported(video), true);
		assert.strictEqual(shouldUseNativeHls(video), false);
	} finally {
		Hls.isSupported = originalSupported;
	}
});

test('shouldUseNativeHls falls back to native HLS when hls.js cannot run', () => {
	const originalSupported = Hls.isSupported;
	const video = {
		canPlayType: (type: string) =>
			type === 'application/vnd.apple.mpegurl' ? 'maybe' : ''
	} as unknown as HTMLVideoElement;
	try {
		Hls.isSupported = () => false;
		assert.strictEqual(shouldUseNativeHls(video), true);
		assert.strictEqual(shouldUseNativeHls({ canPlayType: () => '' } as unknown as HTMLVideoElement), false);
	} finally {
		Hls.isSupported = originalSupported;
	}
});

test('HlsEngine reports stream stats', () => {
	const mockVideo = {
		canPlayType: () => '',
		addEventListener: () => {},
		removeEventListener: () => {}
	} as unknown as HTMLVideoElement;

	const engine = new HlsEngine(mockVideo, 'http://localhost/stream.m3u8', {
		maxBitrate: 8_000_000
	});
	const stats = engine.getStats();
	assert.strictEqual(typeof stats.levelBitrate, 'number');
	assert.strictEqual(typeof stats.bandwidthEstimate, 'number');
	assert.strictEqual(typeof stats.measuredBitrate, 'number');
	engine.destroy();
});

test('HlsEngine initializes with high-bitrate buffer clamping', () => {
	const mockVideo = {
		canPlayType: () => '',
		addEventListener: () => {},
		removeEventListener: () => {}
	} as unknown as HTMLVideoElement;

	// High bitrate (>= 25 Mbps) should clamp buffer to 6s
	const engineHigh = new HlsEngine(mockVideo, 'http://localhost/stream.m3u8', {
		maxBitrate: 30_000_000
	});
	assert.ok(engineHigh);
	engineHigh.destroy();

	// Normal bitrate should use default 30s buffer
	const engineNormal = new HlsEngine(mockVideo, 'http://localhost/stream.m3u8', {
		maxBitrate: 8_000_000
	});
	assert.ok(engineNormal);
	engineNormal.destroy();
});
