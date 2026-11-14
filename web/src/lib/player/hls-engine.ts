/**
 * HLS Playback Engine using hls.js.
 *
 * Implements:
 * 1. Dynamic buffer length clamping for high-bitrate streams (6s clamp >= 25 Mbps).
 * 2. 503 Retry-After backoff integration using createHlsRetryLoaderClass.
 * 3. Media & Network error recovery ladder.
 * 4. Level/quality inspection and selection.
 * 5. Native Safari HLS fallback detection.
 */

import Hls, { type Level, type ErrorData, Events, ErrorTypes } from 'hls.js';
import { createHlsRetryLoaderClass } from '../video-resilience.ts';

export interface HlsQualityLevel {
	index: number;
	label: string;
	height: number;
	bitrate: number;
}

export interface HlsEngineOptions {
	startPositionSeconds?: number;
	maxBitrate?: number;
	onLevelLoaded?: (levels: HlsQualityLevel[]) => void;
	onError?: (error: ErrorData) => void;
}

export function isHlsSupported(): boolean {
	return Hls.isSupported();
}

export function isNativeHlsSupported(video: HTMLVideoElement): boolean {
	return Boolean(
		video.canPlayType('application/vnd.apple.mpegurl') ||
		video.canPlayType('application/x-mpegURL')
	);
}

export class HlsEngine {
	private hls: Hls | null = null;
	private video: HTMLVideoElement | null = null;
	private destroyed = false;
	private mediaRecoveryAttempts = 0;
	private networkRecoveryAttempts = 0;
	private readonly MAX_RECOVERY_ATTEMPTS = 3;

	constructor(
		video: HTMLVideoElement,
		url: string,
		options: HlsEngineOptions = {}
	) {
		this.video = video;

		// Dynamic buffer sizing: high-bitrate video (>= 25 Mbps) on Chromium/Firefox can exhaust memory
		const isHighBitrate = (options.maxBitrate ?? 0) >= 25_000_000;
		const maxBufferLength = isHighBitrate ? 6 : 30;

		const RetryLoader = createHlsRetryLoaderClass(Hls.DefaultConfig.loader);

		this.hls = new Hls({
			startPosition: options.startPositionSeconds ?? -1,
			manifestLoadingTimeOut: 20_000,
			maxBufferLength,
			maxMaxBufferLength: maxBufferLength,
			lowLatencyMode: false,
			backBufferLength: Infinity,
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			loader: RetryLoader as any
		});

		this.hls.subtitleDisplay = true;
		this.hls.attachMedia(video);
		this.hls.loadSource(url);

		this.hls.on(Events.SUBTITLE_TRACKS_UPDATED, (_event, data) => {
			if (this.destroyed || !this.hls) return;
			// A subtitle group only exists when a track was negotiated, so turn
			// the first rendition on instead of leaving hls.js at subtitleTrack=-1.
			if (data.subtitleTracks?.length && this.hls.subtitleTrack < 0) {
				this.hls.subtitleTrack = 0;
			}
		});

		this.hls.on(Events.MANIFEST_PARSED, (_event, data) => {
			if (this.destroyed) return;
			const levels = (data.levels || []).map((lvl: Level, index: number) => ({
				index,
				label: lvl.height ? `${lvl.height}p` : `${Math.round(lvl.bitrate / 1000)} kbps`,
				height: lvl.height,
				bitrate: lvl.bitrate
			}));
			options.onLevelLoaded?.(levels);
		});

		this.hls.on(Events.ERROR, (_event, data: ErrorData) => {
			if (this.destroyed) return;
			if (data.fatal) {
				switch (data.type) {
					case ErrorTypes.NETWORK_ERROR:
						if (this.networkRecoveryAttempts < this.MAX_RECOVERY_ATTEMPTS) {
							this.networkRecoveryAttempts++;
							console.warn(
								`[HlsEngine] Fatal network error, recovering (attempt ${this.networkRecoveryAttempts})...`
							);
							this.hls?.startLoad();
						} else {
							console.error('[HlsEngine] Fatal network error recovery failed');
							options.onError?.(data);
						}
						break;
					case ErrorTypes.MEDIA_ERROR:
						if (this.mediaRecoveryAttempts < this.MAX_RECOVERY_ATTEMPTS) {
							this.mediaRecoveryAttempts++;
							console.warn(
								`[HlsEngine] Fatal media error, recovering (attempt ${this.mediaRecoveryAttempts})...`
							);
							this.hls?.recoverMediaError();
						} else {
							console.error('[HlsEngine] Fatal media error recovery failed');
							options.onError?.(data);
						}
						break;
					default:
						console.error('[HlsEngine] Fatal unrecoverable HLS error:', data.details);
						this.destroy();
						options.onError?.(data);
						break;
				}
			}
		});
	}

	public get currentLevel(): number {
		return this.hls?.currentLevel ?? -1;
	}

	public set currentLevel(levelIndex: number) {
		if (this.hls) {
			this.hls.currentLevel = levelIndex;
		}
	}

	public setQualityLevel(levelIndex: number): void {
		this.currentLevel = levelIndex;
	}

	public get levels(): Level[] {
		return this.hls?.levels ?? [];
	}

	public destroy(): void {
		if (this.destroyed) return;
		this.destroyed = true;
		if (this.hls) {
			try {
				this.hls.destroy();
			} catch {
				/* ignore cleanup errors */
			}
			this.hls = null;
		}
		this.video = null;
	}
}
