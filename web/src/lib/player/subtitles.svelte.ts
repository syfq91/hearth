/**
 * Subtitle Subsystem Controller.
 *
 * Coordinates:
 * 1. WebAssembly client-side ASS/SSA subtitle rendering using JASSUB.
 * 2. Dynamic container font attachment feeding (MediaAttachments).
 * 3. Transcoding offset compensation + user sync calibration (effectiveOffset).
 * 4. Zero-reload track switching between external WebVTT and ASS subtitles.
 * 5. Crash reporting for laddered fallback to server burn-in.
 */

import type JASSUB from 'jassub';
import { toAss } from '../video-subtitles.ts';

export interface SubtitleTrackItem {
	id: string;
	url: string;
	format?: string | null;
	language?: string | null;
	label?: string | null;
}

export interface SubtitleManagerOptions {
	video: HTMLVideoElement;
	onCrash?: (error: unknown) => void;
}

export class SubtitleManager {
	private video: HTMLVideoElement;
	private jassubInstance: JASSUB | null = null;
	private activeTrackElement: HTMLTrackElement | null = null;
	private onCrash?: (error: unknown) => void;

	public activeTrackId = $state<string | null>(null);
	public isAss = $state(false);
	public isLoading = $state(false);
	public userOffsetSeconds = $state(0);
	public transcodingOffsetSeconds = $state(0);

	constructor(options: SubtitleManagerOptions) {
		this.video = options.video;
		this.onCrash = options.onCrash;
	}

	public get effectiveOffset(): number {
		return this.transcodingOffsetSeconds + this.userOffsetSeconds;
	}

	/**
	 * Update user subtitle timing sync offset (in seconds).
	 */
	public setOffset(offsetSeconds: number): void {
		this.userOffsetSeconds = offsetSeconds;
		if (this.jassubInstance) {
			this.jassubInstance.timeOffset = this.effectiveOffset;
			this.renderManualFrame(true);
		}
	}

	/**
	 * Set the server transcoding offset (when transcode begins after 0s).
	 */
	public setTranscodingOffset(offsetSeconds: number): void {
		this.transcodingOffsetSeconds = offsetSeconds;
		if (this.jassubInstance) {
			this.jassubInstance.timeOffset = this.effectiveOffset;
		}
	}

	/**
	 * Load and render a subtitle track without reloading the video stream.
	 */
	public async loadTrack(
		track: SubtitleTrackItem,
		fontUrls: string[] = [],
		transcodingOffsetSeconds = 0
	): Promise<void> {
		this.isLoading = true;
		this.transcodingOffsetSeconds = transcodingOffsetSeconds;

		await this.clearTrack();
		this.activeTrackId = track.id;

		const format = (track.format || '').toLowerCase();
		const isAssFormat = format === 'ass' || format === 'ssa';

		if (isAssFormat) {
			this.isAss = true;
			try {
				const delimiter = track.url.includes('?') ? '&' : '?';
				const fetchUrl = `${track.url}${delimiter}raw=true`;
				const res = await fetch(fetchUrl);
				if (!res.ok) throw new Error(`HTTP ${res.status} fetching subtitles`);
				const text = await res.text();
				const subContent = toAss(text);

				if (this.activeTrackId !== track.id) return;

				const { default: JASSUBConstructor } = await import('jassub');
				const instance = new JASSUBConstructor({
					video: this.video,
					subContent,
					fonts: fontUrls,
					timeOffset: this.effectiveOffset,
					prescaleFactor: 0.8,
					prescaleHeightLimit: 1080,
					maxRenderHeight: 2160,
					libassMemoryLimit: 40,
					libassGlyphLimit: 40
				});

				await instance.ready;
				if (this.activeTrackId === track.id) {
					this.jassubInstance = instance;
					this.renderManualFrame(true);
				} else {
					void instance.destroy();
				}
			} catch (err) {
				console.error('[SubtitleManager] Failed to initialize JASSUB:', err);
				await this.clearTrack();
				this.onCrash?.(err);
			} finally {
				this.isLoading = false;
			}
		} else {
			// WebVTT / native text track
			this.isAss = false;
			try {
				const el = document.createElement('track');
				el.kind = 'subtitles';
				el.label = track.label || track.language || 'Subtitles';
				el.srclang = track.language || '';
				el.src = track.url;
				el.default = true;

				this.video.appendChild(el);
				el.track.mode = 'showing';
				this.activeTrackElement = el;
			} catch (err) {
				console.error('[SubtitleManager] Failed to mount native text track:', err);
			} finally {
				this.isLoading = false;
			}
		}
	}

	/**
	 * Clear any currently active JASSUB or native subtitle track.
	 */
	public async clearTrack(): Promise<void> {
		this.activeTrackId = null;
		this.isAss = false;

		if (this.activeTrackElement) {
			try {
				this.activeTrackElement.track.mode = 'disabled';
				this.video.removeChild(this.activeTrackElement);
			} catch {
				/* ignore DOM errors */
			}
			this.activeTrackElement = null;
		}

		if (this.jassubInstance) {
			const inst = this.jassubInstance;
			this.jassubInstance = null;
			try {
				await inst.destroy();
			} catch {
				/* ignore destroy errors */
			}
		}
	}

	/**
	 * Request a manual render frame on paused seek / resize.
	 */
	public renderManualFrame(repaint = true): void {
		if (!this.jassubInstance || !this.video) return;
		const curTime = this.video.currentTime;
		const w = this.video.videoWidth || this.video.clientWidth;
		const h = this.video.videoHeight || this.video.clientHeight;
		if (w > 0 && h > 0) {
			void this.jassubInstance.manualRender(
				{
					mediaTime: curTime,
					width: w,
					height: h,
					expectedDisplayTime: performance.now()
				},
				repaint
			);
		}
	}

	/**
	 * Resize the canvas overlay when video container bounds change.
	 */
	public resize(): void {
		if (this.jassubInstance) {
			void this.jassubInstance.resize();
			this.renderManualFrame(true);
		}
	}

	/**
	 * Full cleanup on player unmount.
	 */
	public async destroy(): Promise<void> {
		await this.clearTrack();
	}
}
