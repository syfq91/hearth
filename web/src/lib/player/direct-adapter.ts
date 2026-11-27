import type { MediaAdapter, MediaAdapterCallbacks } from './media-adapter.ts';

export interface DirectPlayAdapterOptions {
	url: string;
	startPositionSeconds?: number;
	callbacks?: MediaAdapterCallbacks;
}

export class DirectPlayAdapter implements MediaAdapter {
	private video: HTMLVideoElement | null = null;
	private url: string;
	private startPositionSeconds: number;
	private callbacks: MediaAdapterCallbacks;
	private resumeApplied = false;
	private destroyed = false;
	private cleanupListeners: (() => void) | null = null;

	constructor(options: DirectPlayAdapterOptions) {
		this.url = options.url;
		this.startPositionSeconds = options.startPositionSeconds ?? 0;
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
			if (!this.resumeApplied && this.startPositionSeconds > 0) {
				this.resumeApplied = true;
				video.currentTime = this.startPositionSeconds;
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
				this.callbacks.onError?.(video.error.message || 'Media decode failed');
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

		video.src = this.url;
	}

	public async seek(rawTargetSeconds: number): Promise<void> {
		if (!this.video || this.destroyed) return;
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

	public destroy(): void {
		if (this.destroyed) return;
		this.destroyed = true;
		this.cleanupListeners?.();
		this.cleanupListeners = null;
		this.video = null;
	}
}
