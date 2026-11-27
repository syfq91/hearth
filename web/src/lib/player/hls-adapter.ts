import { api } from '../api/client.ts';
import {
	HlsEngine,
	isHlsSupported,
	shouldUseNativeHls
} from './hls-engine.ts';
import type { MediaAdapter, MediaAdapterCallbacks } from './media-adapter.ts';

export interface HlsAdapterOptions {
	url: string;
	sessionId?: string | null;
	isRemux?: boolean;
	startPositionSeconds?: number;
	maxBitrate?: number;
	debug?: boolean;
	callbacks?: MediaAdapterCallbacks;
}

export class HlsAdapter implements MediaAdapter {
	private video: HTMLVideoElement | null = null;
	private engine: HlsEngine | null = null;
	private options: HlsAdapterOptions;
	private callbacks: MediaAdapterCallbacks;
	private destroyed = false;
	private seekAbort: AbortController | null = null;
	private cleanupListeners: (() => void) | null = null;

	constructor(options: HlsAdapterOptions) {
		this.options = options;
		this.callbacks = options.callbacks ?? {};
	}

	public attach(video: HTMLVideoElement): void {
		if (this.destroyed) return;
		this.video = video;

		const onLoadedMetadata = () => {
			if (this.destroyed) return;
			if (Number.isFinite(video.duration) && video.duration > 0) {
				this.callbacks.onDurationChange?.(video.duration);
			}
			this.callbacks.onReady?.();
		};
		const onDurationChange = () => {
			if (Number.isFinite(video.duration) && video.duration > 0) {
				this.callbacks.onDurationChange?.(video.duration);
			}
		};
		const onTimeUpdate = () => this.callbacks.onTimeUpdate?.(video.currentTime);
		const onWaiting = () => this.callbacks.onBuffering?.(true);
		const onCanPlay = () => this.callbacks.onBuffering?.(false);
		const onPlaying = () => this.callbacks.onBuffering?.(false);
		const onPlay = () => this.callbacks.onPlayStateChange?.(false);
		const onPause = () => this.callbacks.onPlayStateChange?.(true);
		const onSeeked = () => this.callbacks.onSeeked?.();
		const onEnded = () => {
			this.callbacks.onPlayStateChange?.(true);
			this.callbacks.onEnded?.();
		};
		const onProgress = () => this.callbacks.onProgress?.();
		const onError = () => {
			if (video.error) {
				this.callbacks.onError?.(video.error.message || 'HLS decode failed');
			}
		};

		video.addEventListener('loadedmetadata', onLoadedMetadata);
		video.addEventListener('durationchange', onDurationChange);
		video.addEventListener('timeupdate', onTimeUpdate);
		video.addEventListener('waiting', onWaiting);
		video.addEventListener('canplay', onCanPlay);
		video.addEventListener('playing', onPlaying);
		video.addEventListener('play', onPlay);
		video.addEventListener('pause', onPause);
		video.addEventListener('seeked', onSeeked);
		video.addEventListener('ended', onEnded);
		video.addEventListener('progress', onProgress);
		video.addEventListener('error', onError);

		this.cleanupListeners = () => {
			video.removeEventListener('loadedmetadata', onLoadedMetadata);
			video.removeEventListener('durationchange', onDurationChange);
			video.removeEventListener('timeupdate', onTimeUpdate);
			video.removeEventListener('waiting', onWaiting);
			video.removeEventListener('canplay', onCanPlay);
			video.removeEventListener('playing', onPlaying);
			video.removeEventListener('play', onPlay);
			video.removeEventListener('pause', onPause);
			video.removeEventListener('seeked', onSeeked);
			video.removeEventListener('ended', onEnded);
			video.removeEventListener('progress', onProgress);
			video.removeEventListener('error', onError);
		};

		if (!shouldUseNativeHls(video) && isHlsSupported()) {
			this.engine = new HlsEngine(video, this.options.url, {
				startPositionSeconds:
					this.options.startPositionSeconds && this.options.startPositionSeconds > 0
						? this.options.startPositionSeconds
						: -1,
				maxBitrate: this.options.maxBitrate,
				debug: this.options.debug,
				onLevelLoaded: (levels) => this.callbacks.onLevelLoaded?.(levels),
				onStats: (stats) => this.callbacks.onStats?.(stats),
				onError: (err) => {
					this.callbacks.onError?.(`Playback error: ${err.details || 'stream failure'}`);
				}
			});
		} else {
			video.src = this.options.url;
			if (this.options.startPositionSeconds && this.options.startPositionSeconds > 0) {
				video.currentTime = this.options.startPositionSeconds;
			}
		}
	}

	private isBuffered(seconds: number): boolean {
		if (!this.video) return false;
		const buf = this.video.buffered;
		for (let i = 0; i < buf.length; i++) {
			if (seconds >= buf.start(i) && seconds <= buf.end(i)) return true;
		}
		return false;
	}

	public async seek(rawTargetSeconds: number): Promise<void> {
		if (!this.video || this.destroyed) return;
		const sessionId = this.options.sessionId;
		if (!sessionId || this.isBuffered(rawTargetSeconds)) {
			this.video.currentTime = rawTargetSeconds;
			return;
		}

		// Coordinate server-side ffmpeg seek before moving playhead so hls.js
		// does not race against an in-flight process restart.
		if (this.seekAbort) {
			this.seekAbort.abort();
		}
		const controller = new AbortController();
		this.seekAbort = controller;

		try {
			await api.post(`/hls/${sessionId}/seek`, {
				body: { position_seconds: rawTargetSeconds },
				signal: controller.signal
			});
		} catch {
			if (controller.signal.aborted || this.destroyed) return;
		}

		if (this.destroyed || controller.signal.aborted || !this.video) return;

		if (this.options.isRemux && this.engine) {
			// Remux playlists carry variable keyframe #EXTINF durations; reload
			// the playlist at the new position so hls.js aligns with the new tfdt.
			this.engine.destroy();
			this.engine = new HlsEngine(this.video, this.options.url, {
				startPositionSeconds: rawTargetSeconds,
				maxBitrate: this.options.maxBitrate,
				debug: this.options.debug,
				onLevelLoaded: (levels) => this.callbacks.onLevelLoaded?.(levels),
				onStats: (stats) => this.callbacks.onStats?.(stats),
				onError: (err) => {
					this.callbacks.onError?.(`Playback error: ${err.details || 'stream failure'}`);
				}
			});
			return;
		}

		this.video.currentTime = rawTargetSeconds;
	}

	public async play(): Promise<void> {
		if (!this.video || this.destroyed) return;
		await this.video.play();
	}

	public pause(): void {
		if (!this.video || this.destroyed) return;
		this.video.pause();
	}

	public setPlaybackRate(rate: number): void {
		if (!this.video || this.destroyed) return;
		this.video.playbackRate = rate;
	}

	public setQualityLevel(levelIndex: number): void {
		this.engine?.setQualityLevel(levelIndex);
	}

	public destroy(): void {
		if (this.destroyed) return;
		this.destroyed = true;
		if (this.seekAbort) {
			this.seekAbort.abort();
			this.seekAbort = null;
		}
		this.cleanupListeners?.();
		this.cleanupListeners = null;
		this.engine?.destroy();
		this.engine = null;
		this.video = null;
	}
}
