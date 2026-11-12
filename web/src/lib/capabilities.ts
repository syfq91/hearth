/**
 * Browser capability detection and client playback profile resolution.
 *
 * Employs MediaSource.isTypeSupported and HTMLMediaElement.canPlayType to probe
 * the client environment for container and codec support, caching the result
 * across the page session.
 */

import type { S } from './api/types';

export type ClientCapabilities = S['ClientCapabilities'];

let cachedCapabilities: ClientCapabilities | null = null;

/**
 * Reset the cached capabilities. Primarily useful in automated test suites.
 */
export function clearCapabilitiesCache(): void {
	cachedCapabilities = null;
}

/**
 * Detect client browser playback capabilities for video/audio codecs and containers.
 */
export function detectBrowserCapabilities(): ClientCapabilities {
	if (cachedCapabilities) {
		return cachedCapabilities;
	}

	const win = typeof window !== 'undefined' ? (window as unknown as Record<string, unknown>) : null;
	const doc = typeof document !== 'undefined' ? (document as unknown as Document) : null;

	const msObj = win?.MediaSource as { isTypeSupported?: (mime: string) => boolean } | undefined;
	const hasMediaSource = typeof msObj === 'function' || (typeof msObj === 'object' && msObj !== null);
	const isTypeSupportedFn = typeof msObj?.isTypeSupported === 'function' ? msObj.isTypeSupported.bind(msObj) : null;

	let canPlay: (type: string) => boolean;
	let isHlsNative = false;

	if (isTypeSupportedFn) {
		canPlay = (type: string) => {
			try {
				return isTypeSupportedFn(type);
			} catch {
				return false;
			}
		};
	} else {
		// Fall back to HTMLMediaElement canPlayType (e.g. iOS Safari)
		let videoEl: HTMLVideoElement | null = null;
		if (doc && typeof doc.createElement === 'function') {
			try {
				videoEl = doc.createElement('video');
			} catch {
				videoEl = null;
			}
		}

		canPlay = (type: string) => {
			if (!videoEl || typeof videoEl.canPlayType !== 'function') return false;
			try {
				const res = videoEl.canPlayType(type);
				return res === 'probably' || res === 'maybe';
			} catch {
				return false;
			}
		};

		if (videoEl && typeof videoEl.canPlayType === 'function') {
			try {
				const hlsRes = videoEl.canPlayType('application/vnd.apple.mpegurl');
				isHlsNative = hlsRes === 'probably' || hlsRes === 'maybe';
			} catch {
				isHlsNative = false;
			}
		}
	}

	const videoCodecs: string[] = [];
	const videoProfiles: string[] = [];
	const audioCodecs: string[] = [];
	const containers: string[] = [];

	// --- 1. Video Codecs ---
	const supportsH264Baseline = canPlay('video/mp4; codecs="avc1.42E01E"');
	const supportsH264Main = canPlay('video/mp4; codecs="avc1.4D401E"');
	const supportsH264High = canPlay('video/mp4; codecs="avc1.640028"');
	const supportsH264Generic = canPlay('video/mp4; codecs="avc1"');

	if (supportsH264Baseline || supportsH264Main || supportsH264High || supportsH264Generic) {
		videoCodecs.push('h264');
		if (supportsH264Baseline) videoProfiles.push('baseline');
		if (supportsH264Main) videoProfiles.push('main');
		if (supportsH264High) videoProfiles.push('high');
		if (videoProfiles.length === 0) {
			videoProfiles.push('baseline', 'main', 'high');
		}
	}

	// HEVC / H.265
	const supportsHevcGeneric = canPlay('video/mp4; codecs="hvc1"') || canPlay('video/mp4; codecs="hev1"');
	const supportsHevcL93 = canPlay('video/mp4; codecs="hvc1.1.6.L93.B0"') || canPlay('video/mp4; codecs="hev1.1.6.L93.B0"');
	const supportsHevcMain10 = canPlay('video/mp4; codecs="hvc1.2.4.L150.B0"') || canPlay('video/mp4; codecs="hev1.2.4.L150.B0"');

	if (supportsHevcGeneric || supportsHevcL93 || supportsHevcMain10) {
		videoCodecs.push('hevc');
		if (supportsHevcMain10) {
			videoProfiles.push('main 10');
		}
	}

	// VP9
	if (canPlay('video/webm; codecs="vp9"') || canPlay('video/mp4; codecs="vp09.00.10.08"')) {
		videoCodecs.push('vp9');
	}

	// AV1
	if (canPlay('video/mp4; codecs="av01.0.08M.08"') || canPlay('video/webm; codecs="av01.0.08M.08"')) {
		videoCodecs.push('av1');
	}

	// --- 2. Audio Codecs ---
	if (canPlay('audio/mp4; codecs="mp4a.40.2"') || canPlay('audio/aac')) {
		audioCodecs.push('aac');
	}
	if (canPlay('audio/webm; codecs="opus"') || canPlay('audio/ogg; codecs="opus"') || canPlay('audio/mp4; codecs="opus"')) {
		audioCodecs.push('opus');
	}
	if (canPlay('audio/mpeg') || canPlay('audio/mp4; codecs="mp4a.40.34"')) {
		audioCodecs.push('mp3');
	}
	if (canPlay('audio/flac') || canPlay('audio/mp4; codecs="flac"')) {
		audioCodecs.push('flac');
	}
	if (canPlay('audio/mp4; codecs="ac-3"')) {
		audioCodecs.push('ac3');
	}
	if (canPlay('audio/mp4; codecs="ec-3"')) {
		audioCodecs.push('eac3');
	}

	// --- 3. Containers ---
	if (canPlay('video/mp4') || videoCodecs.includes('h264') || audioCodecs.includes('aac')) {
		containers.push('mp4');
	}
	if (canPlay('video/webm') || audioCodecs.includes('opus') || videoCodecs.includes('vp9')) {
		containers.push('webm');
	}
	if (canPlay('audio/mp4') || audioCodecs.includes('aac')) {
		containers.push('m4a');
	}
	if (audioCodecs.includes('mp3') || canPlay('audio/mpeg')) {
		containers.push('mp3');
	}
	if (audioCodecs.includes('flac')) {
		containers.push('flac');
	}

	// fMP4 HLS support: MediaSource capable of fMP4 or Safari native HLS
	const hlsFmp4 = isTypeSupportedFn ? (supportsH264Baseline || supportsH264High || canPlay('video/mp4')) : isHlsNative;

	// HDR check via media query
	let hdr = false;
	const matchMediaFn = win?.matchMedia as ((query: string) => { matches: boolean }) | undefined;
	if (typeof matchMediaFn === 'function') {
		try {
			hdr = matchMediaFn('(dynamic-range: high)').matches;
		} catch {
			hdr = false;
		}
	}

	// Guard for environments lacking both MediaSource and DOM (or returning nothing)
	if (videoCodecs.length === 0 && !hasMediaSource) {
		videoCodecs.push('h264');
		videoProfiles.push('baseline', 'main', 'high');
		if (!audioCodecs.includes('aac')) audioCodecs.push('aac');
		if (!containers.includes('mp4')) containers.push('mp4');
	}

	const caps: ClientCapabilities = {
		containers,
		video_codecs: videoCodecs,
		video_profiles: videoProfiles,
		audio_codecs: audioCodecs,
		subtitle_formats: ['vtt'],
		hdr,
		hls_fmp4: hlsFmp4
	};

	cachedCapabilities = caps;
	return caps;
}
