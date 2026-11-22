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
	/** Emit timeline diagnostics for resume/buffer investigations. */
	debug?: boolean;
	onLevelLoaded?: (levels: HlsQualityLevel[]) => void;
	onError?: (error: ErrorData) => void;
	onStats?: (stats: HlsStreamStats) => void;
}

export interface HlsStreamStats {
	/** Nominal bitrate of the rendition currently playing, bits/s. */
	levelBitrate: number;
	/** hls.js smoothed bandwidth estimate, bits/s. */
	bandwidthEstimate: number;
	/** Throughput measured for the last fully loaded segment, bits/s. */
	measuredBitrate: number;
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

/**
 * Decide whether to hand an HLS URL straight to the native `<video>` element.
 *
 * Native demuxers answer "maybe" to canPlayType() but have no Retry-After
 * loader for the JSON 503s Hearth emits while a transcode segment is being
 * generated, and they cannot be given a start position. On a cold resume the
 * first segment request can 503, after which a native player stalls with the
 * playhead stranded outside the buffered range. hls.js handles both (it has a
 * retry loader and `startPosition`), so prefer it wherever MSE is available.
 * Native HLS is only a fallback for engines where hls.js cannot run.
 */
export function shouldUseNativeHls(video: HTMLVideoElement): boolean {
	if (Hls.isSupported()) return false;
	return isNativeHlsSupported(video);
}

export class HlsEngine {
	private hls: Hls | null = null;
	private video: HTMLVideoElement | null = null;
	private destroyed = false;
	private mediaRecoveryAttempts = 0;
	private networkRecoveryAttempts = 0;
	private lastMeasuredBitrate = 0;
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

		if (options.debug) {
			const log = (event: string, detail: Record<string, unknown>) =>
				console.debug('[hearth:hls]', event, detail);
			this.hls.on(Events.MANIFEST_PARSED, () =>
				log('manifestParsed', { startPosition: options.startPositionSeconds })
			);
			this.hls.on(Events.INIT_PTS_FOUND, (_event, data) =>
				log('initPtsFound', {
					sn: data.frag.sn,
					fragStart: data.frag.start,
					initPTS: data.initPTS,
					timescale: data.timescale
				})
			);
			this.hls.on(Events.BUFFER_APPENDED, (_event, data) =>
				log('bufferAppended', { type: data.type, sn: data.frag.sn, timeRanges: data.timeRanges })
			);
		}

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
			this.emitStats(options);
		});

		this.hls.on(Events.LEVEL_SWITCHED, () => {
			if (this.destroyed) return;
			this.emitStats(options);
		});

		this.hls.on(Events.FRAG_LOADED, (_event, data) => {
			if (this.destroyed) return;
			// Throughput of the segment just fetched; hls.js only exposes it on
			// the fragment's loader stats.
			const stats = data.frag.stats;
			const elapsedMs = stats.loading.end - stats.loading.first;
			if (elapsedMs > 0 && stats.loaded > 0) {
				this.lastMeasuredBitrate = (stats.loaded * 8000) / elapsedMs;
			}
			this.emitStats(options);
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

	public getStats(): HlsStreamStats {
		const current = this.hls?.currentLevel ?? -1;
		const level = current >= 0 ? this.hls?.levels?.[current] : undefined;
		return {
			levelBitrate: level?.bitrate ?? 0,
			bandwidthEstimate: this.hls?.bandwidthEstimate ?? 0,
			measuredBitrate: this.lastMeasuredBitrate
		};
	}

	private emitStats(options: HlsEngineOptions): void {
		options.onStats?.(this.getStats());
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
