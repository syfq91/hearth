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

/**
 * A valid, empty ASS document used to let libass finish initialising (including
 * the container font catalog) while the real track is still downloading.
 */
const EMPTY_ASS = [
	'[Script Info]',
	'ScriptType: v4.00+',
	'',
	'[V4+ Styles]',
	'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
	'Style: Default,Liberation Sans,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1',
	'',
	'[Events]',
	'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text'
].join('\n');

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
	/** The mounted track, kept so an offset change can remount a native track. */
	private activeTrack: SubtitleTrackItem | null = null;
	private activeFontUrls: string[] = [];
	private offsetRemountTimer: ReturnType<typeof setTimeout> | null = null;
	/** Non-zero container start PTS (seconds) subtracted from raw video.currentTime. */
	private mediaStartSeconds = 0;

	public activeTrackId = $state<string | null>(null);
	public isAss = $state(false);
	public isLoading = $state(false);
	public userOffsetSeconds = $state(0);

	constructor(options: SubtitleManagerOptions) {
		this.video = options.video;
		this.onCrash = options.onCrash;
	}

	/**
	 * Set the container's presentation start offset (in seconds) so 0-based
	 * subtitle cues align with raw container timestamps on `<video>`.
	 */
	public setMediaStartSeconds(seconds: number): void {
		const normalized = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
		if (this.mediaStartSeconds === normalized) return;
		this.mediaStartSeconds = normalized;
		if (this.jassubInstance) {
			this.jassubInstance.timeOffset = this.effectiveJassubOffset();
			this.renderManualFrame(true);
			return;
		}
		this.scheduleOffsetRemount();
	}

	/**
	 * JASSUB evaluates cues at ``mediaTime + timeOffset``. Subtracting both
	 * ``mediaStartSeconds`` and ``userOffsetSeconds`` aligns 0-based cues with
	 * raw container timestamps and makes positive user offsets delay subtitles
	 * (matching SubtitleSyncControl).
	 */
	private effectiveJassubOffset(): number {
		return -this.mediaStartSeconds - this.userOffsetSeconds;
	}

	/**
	 * Update user subtitle timing sync offset (in seconds).
	 * Negative values display subtitles earlier; positive values delay them.
	 */
	public setOffset(offsetSeconds: number): void {
		if (this.userOffsetSeconds === offsetSeconds) return;
		this.userOffsetSeconds = offsetSeconds;
		if (this.jassubInstance) {
			this.jassubInstance.timeOffset = this.effectiveJassubOffset();
			this.renderManualFrame(true);
			return;
		}
		// Native text tracks resolve cues against their own timestamps, so the
		// only way to shift them is to re-fetch the document with a new shift.
		this.scheduleOffsetRemount();
	}

	private scheduleOffsetRemount(): void {
		const track = this.activeTrack;
		if (!track || this.isAss) return;
		if (this.offsetRemountTimer) clearTimeout(this.offsetRemountTimer);
		this.offsetRemountTimer = setTimeout(() => {
			this.offsetRemountTimer = null;
			if (this.isAss || this.activeTrack?.id !== track.id) return;
			void this.loadTrack(track, this.activeFontUrls);
		}, 200);
	}

	/**
	 * Native cue times are adjusted server-side. Positive ``shift_ms`` moves
	 * cues later, compensating for ``mediaStartSeconds`` and delaying cues when
	 * ``userOffsetSeconds`` is positive.
	 */
	private nativeTrackUrl(track: SubtitleTrackItem): string {
		const shiftMs = Math.round((this.mediaStartSeconds + this.userOffsetSeconds) * 1000);
		if (shiftMs === 0) return track.url;
		const separator = track.url.includes('?') ? '&' : '?';
		return `${track.url}${separator}shift_ms=${shiftMs}`;
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
		this.activeTrack = track;
		this.activeFontUrls = fontUrls;

		const format = (track.format || '').toLowerCase();
		if (format === 'ass' || format === 'ssa') {
			try {
				const delimiter = track.url.includes('?') ? '&' : '?';
				const fetchUrl = `${track.url}${delimiter}raw=true`;
				// Fetch the document while JASSUB initializes its worker,
				// WebAssembly and font catalog. Waiting for the document first
				// serialized two network/CPU phases and delayed the first cue.
				const trackPromise = fetch(fetchUrl).then(async (response) => {
					if (!response.ok) throw new Error(`HTTP ${response.status} fetching subtitles`);
					return response.text();
				});
				void trackPromise.catch(() => {
					/* the awaited path reports a useful error */
				});
				const modulePromise = import('jassub');
				void modulePromise.catch(() => {
					/* the awaited path reports a useful error */
				});

				const { default: JASSUBConstructor } = await modulePromise;
				if (generation !== this.loadGeneration) return;

				this.isAss = true;
				const instance = new JASSUBConstructor({
					video: this.video,
					subContent: EMPTY_ASS,
					fonts: fontUrls,
					timeOffset: this.effectiveJassubOffset(),
					prescaleFactor: 0.8,
					prescaleHeightLimit: 1080,
					maxRenderHeight: 2160,
					libassMemoryLimit: 40,
					libassGlyphLimit: 40
				});

				try {
					const [text] = await Promise.all([trackPromise, instance.ready]);
					if (generation !== this.loadGeneration) {
						void instance.destroy();
						return;
					}
					await instance.renderer.setTrack(toAss(text));
					instance.timeOffset = this.effectiveJassubOffset();
					this.jassubInstance = instance;
					this.renderManualFrame(true);
				} catch (error) {
					void instance.destroy();
					throw error;
				}
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
			el.src = this.nativeTrackUrl(track);
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
		this.activeTrack = null;
		this.activeFontUrls = [];
		if (this.offsetRemountTimer) {
			clearTimeout(this.offsetRemountTimer);
			this.offsetRemountTimer = null;
		}

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
