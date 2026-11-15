/**
 * Subtitle Subsystem Controller.
 *
 * Coordinates:
 * 1. WebAssembly client-side ASS/SSA subtitle rendering using JASSUB.
 * 2. Dynamic container font attachment feeding (MediaAttachments).
 * 3. User sync calibration applied to whichever renderer is active.
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
	/** Bumped on every mount/clear so a superseded async load cannot mount. */
	private loadGeneration = 0;
	/** Serializes mounts and clears so only one track is ever attached. */
	private switchChain: Promise<void> = Promise.resolve();

	public activeTrackId = $state<string | null>(null);
	public isAss = $state(false);
	public isLoading = $state(false);
	public userOffsetSeconds = $state(0);

	constructor(options: SubtitleManagerOptions) {
		this.video = options.video;
		this.onCrash = options.onCrash;
	}

	/**
	 * Update user subtitle timing sync offset (in seconds).
	 */
	public setOffset(offsetSeconds: number): void {
		this.userOffsetSeconds = offsetSeconds;
		if (this.jassubInstance) {
			this.jassubInstance.timeOffset = this.userOffsetSeconds;
			this.renderManualFrame(true);
		}
	}

	/**
	 * Load and render a subtitle track without reloading the video stream.
	 *
	 * Calls are serialized and tagged with a generation: a load superseded by a
	 * newer selection or by {@link clearTrack} bails out after every await, so
	 * an overlapping switch cannot leave two tracks mounted.
	 */
	public loadTrack(track: SubtitleTrackItem, fontUrls: string[] = []): Promise<void> {
		const generation = ++this.loadGeneration;
		this.isLoading = true;
		const run = this.switchChain.then(() => this.mount(track, fontUrls, generation));
		this.switchChain = run.catch(() => {});
		return run;
	}

	private async mount(
		track: SubtitleTrackItem,
		fontUrls: string[],
		generation: number
	): Promise<void> {
		await this.detach();
		if (generation !== this.loadGeneration) return;
		this.activeTrackId = track.id;

		const format = (track.format || '').toLowerCase();
		if (format === 'ass' || format === 'ssa') {
			try {
				const delimiter = track.url.includes('?') ? '&' : '?';
				const fetchUrl = `${track.url}${delimiter}raw=true`;
				const res = await fetch(fetchUrl);
				if (!res.ok) throw new Error(`HTTP ${res.status} fetching subtitles`);
				const text = await res.text();
				const subContent = toAss(text);
				if (generation !== this.loadGeneration) return;

				const { default: JASSUBConstructor } = await import('jassub');
				if (generation !== this.loadGeneration) return;

				this.isAss = true;
				const instance = new JASSUBConstructor({
					video: this.video,
					subContent,
					fonts: fontUrls,
					timeOffset: this.userOffsetSeconds,
					prescaleFactor: 0.8,
					prescaleHeightLimit: 1080,
					maxRenderHeight: 2160,
					libassMemoryLimit: 40,
					libassGlyphLimit: 40
				});

				await instance.ready;
				if (generation !== this.loadGeneration) {
					void instance.destroy();
					return;
				}
				this.jassubInstance = instance;
				this.renderManualFrame(true);
			} catch (err) {
				console.error('[SubtitleManager] Failed to initialize JASSUB:', err);
				if (generation === this.loadGeneration) {
					await this.detach();
					this.onCrash?.(err);
				}
			} finally {
				if (generation === this.loadGeneration) this.isLoading = false;
			}
			return;
		}

		// WebVTT / native text track
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
			if (generation === this.loadGeneration) this.isLoading = false;
		}
	}

	/**
	 * Clear any currently active JASSUB or native subtitle track.
	 */
	public clearTrack(): Promise<void> {
		++this.loadGeneration;
		this.isLoading = false;
		const run = this.switchChain.then(() => this.detach());
		this.switchChain = run.catch(() => {});
		return run;
	}

	/** Remove whatever is mounted. Callers must be on the switch chain. */
	private async detach(): Promise<void> {
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

		const inst = this.jassubInstance;
		this.jassubInstance = null;
		if (inst) {
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
