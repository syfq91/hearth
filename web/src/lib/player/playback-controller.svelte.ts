import { api } from '../api/client.ts';
import type { PlaybackInfoRequest, PlaybackInfoResponse } from '../api/types.ts';
import { session } from '../auth.svelte.ts';
import { detectBrowserCapabilities, getCapabilitiesWithBurnInFallback } from '../capabilities.ts';
import { resolveAutoSubtitle } from '../languages.ts';
import { sendStopBeacon } from '../video-resilience.ts';
import { attachmentFontUrls } from '../video-subtitles.ts';
import { DirectPlayAdapter } from './direct-adapter.ts';
import { HlsAdapter } from './hls-adapter.ts';
import type { HlsStreamStats } from './hls-engine.ts';
import type { MediaAdapter, MediaAdapterCallbacks } from './media-adapter.ts';
import { contentDuration, contentTime } from './seekbar.ts';
import { SubtitleManager } from './subtitles.svelte.ts';

export type QualityLevel = NonNullable<PlaybackInfoRequest['quality']>;

const REPORT_INTERVAL_MS = 10_000;

export interface PlaybackControllerOptions {
	itemId: string;
	startAtMs?: number;
	debug?: boolean;
	onEnded?: () => void;
}

export class PlaybackController {
	public itemId: string;
	public startAtMs: number;
	private debug: boolean;
	private onEnded?: () => void;

	private videoEl: HTMLVideoElement | null = null;
	private adapter: MediaAdapter | null = null;
	public subtitleManager = $state<SubtitleManager | null>(null);

	public info = $state<PlaybackInfoResponse | null>(null);
	public loading = $state(true);
	public loadError = $state<string | null>(null);
	public isWaiting = $state(false);
	public isPaused = $state(true);

	public currentTime = $state(0);
	public rawDuration = $state(0);
	public expectedDuration = $state(0);
	public bufferedRanges = $state<{ startPct: number; widthPct: number }[]>([]);

	public volume = $state(1);
	public isMuted = $state(false);
	public playbackRate = $state(1);
	public quality = $state<QualityLevel>('auto');
	public streamStats = $state<HlsStreamStats | null>(null);

	public audioTrackIndex = $state<number | null>(null);
	public subtitleChoice = $state<string>('');
	public currentSubOffset = $state(0);
	public nowMs = $state(Date.now());

	public sessionId: string | null = null;
	public sessionMediaToken: string | null = null;

	private embeddedSubtitleIndex: number | null = null;
	private forceBurnIn = false;
	private resumePositionMs = 0;
	private lastReportWallMs = 0;
	private destroyed = false;
	private startGeneration = 0;
	private triedVttFallbackFor = new Set<string>();

	constructor(options: PlaybackControllerOptions) {
		this.itemId = options.itemId;
		this.startAtMs = options.startAtMs ?? 0;
		this.debug = options.debug ?? false;
		this.onEnded = options.onEnded;
	}

	public get mediaStartSec(): number {
		return this.info?.start_time_seconds ?? 0;
	}

	public get mediaDurationRaw(): number {
		if (this.rawDuration > 0 && Number.isFinite(this.rawDuration)) return this.rawDuration;
		if (this.expectedDuration > 0) return this.expectedDuration;
		return 0;
	}

	public get duration(): number {
		return contentDuration(this.mediaDurationRaw, this.mediaStartSec);
	}

	public get progressPct(): number {
		if (this.duration <= 0) return 0;
		return Math.min(100, Math.max(0, (this.currentTime / this.duration) * 100));
	}

	public displayTime(rawSeconds: number): number {
		return contentTime(rawSeconds, this.mediaStartSec);
	}

	public attachVideo(video: HTMLVideoElement): void {
		this.videoEl = video;
		void this.startPlayback();
	}

	public switchItem(itemId: string, startAtMs = 0): void {
		this.itemId = itemId;
		this.startAtMs = startAtMs;
		this.audioTrackIndex = null;
		this.subtitleChoice = '';
		this.resumePositionMs = 0;
		this.info = null;
		this.forceBurnIn = false;
		this.triedVttFallbackFor.clear();
		if (this.videoEl) {
			void this.startPlayback();
		}
	}

	public async startPlayback(options: { forceBurnIn?: boolean } = {}): Promise<void> {
		if (!this.videoEl) return;
		const generation = ++this.startGeneration;
		this.destroyed = false;
		this.loading = true;
		this.loadError = null;
		this.expectedDuration = 0;

		if (options.forceBurnIn) {
			this.forceBurnIn = true;
		}

		this.stopActiveSession();

		this.embeddedSubtitleIndex = this.subtitleChoice.startsWith('emb:')
			? Number.parseInt(this.subtitleChoice.slice('emb:'.length), 10)
			: null;
		const externalSubtitleId = this.subtitleChoice.startsWith('ext:')
			? this.subtitleChoice.slice('ext:'.length)
			: null;

		const cur = this.videoEl.currentTime ?? 0;
		if (cur > 0) {
			this.resumePositionMs = Math.floor(cur * 1000);
		} else if (this.startAtMs > 0) {
			this.resumePositionMs = this.startAtMs;
		}
		const offsetSeconds = this.resumePositionMs > 0 ? this.resumePositionMs / 1000 : 0;

		const clientCapabilities = this.forceBurnIn
			? getCapabilitiesWithBurnInFallback()
			: detectBrowserCapabilities();

		let response: PlaybackInfoResponse;
		try {
			response = await api.post<PlaybackInfoResponse>(`/items/${this.itemId}/playback-info`, {
				body: {
					capabilities: clientCapabilities,
					audio_track_index: this.audioTrackIndex,
					subtitle_track_index: this.embeddedSubtitleIndex,
					subtitle_id: externalSubtitleId,
					start_time_offset_seconds: offsetSeconds,
					force_burn_in_subtitles: this.forceBurnIn,
					quality: this.quality
				}
			});
		} catch (e) {
			if (generation !== this.startGeneration || this.destroyed) return;
			this.loadError = e instanceof Error ? e.message : String(e);
			this.loading = false;
			return;
		}

		if (generation !== this.startGeneration || this.destroyed) return;
		this.info = response;

		if (this.audioTrackIndex === null && response.plan?.audio_stream_index != null) {
			this.audioTrackIndex = response.plan.audio_stream_index;
		}

		if (this.subtitleChoice === '') {
			const activeAudio =
				(response.audio_streams ?? []).find((s) => s.index === this.audioTrackIndex) ??
				(response.audio_streams ?? []).find((s) => s.is_default) ??
				response.audio_streams?.[0];
			const autoSub = resolveAutoSubtitle({
				subtitles: response.subtitles,
				embeddedSubtitles: response.embedded_subtitles,
				activeAudio,
				preferredAudioLanguage: session.user?.preferred_audio_language,
				preferredSubtitleLanguage: session.user?.preferred_subtitle_language,
				subtitleMode: session.user?.subtitle_mode
			});
			if (autoSub) {
				this.subtitleChoice = autoSub;
				const autoNeedsServerBurnIn =
					(autoSub.startsWith('emb:') &&
						!(response.embedded_subtitle_urls ?? []).some(
							(e) => e.index === Number.parseInt(autoSub.slice('emb:'.length), 10)
						)) ||
					(autoSub.startsWith('ext:') &&
						this.sidecarRequiresBurnIn(autoSub.slice('ext:'.length), response));
				if (autoNeedsServerBurnIn) {
					await this.startPlayback();
					return;
				}
			}
		}

		this.sessionId = response.session_id ?? null;
		if (response.hls_url) {
			try {
				const parsed = new URL(response.hls_url, window.location.origin);
				this.sessionMediaToken = parsed.searchParams.get('token');
			} catch {
				this.sessionMediaToken = null;
			}
		}

		this.attachSource(response);
	}

	private stopActiveSession(): void {
		const previousSession = this.sessionId;
		const previousToken = this.sessionMediaToken;
		this.sessionId = null;
		this.sessionMediaToken = null;
		if (previousSession) {
			sendStopBeacon(previousSession, previousToken);
		}
	}

	private attachSource(response: PlaybackInfoResponse): void {
		if (!this.videoEl) return;
		this.lastReportWallMs = 0;
		this.streamStats = null;
		this.rawDuration = 0;

		if (this.adapter) {
			this.adapter.destroy();
			this.adapter = null;
		}

		if (!this.subtitleManager) {
			this.subtitleManager = new SubtitleManager({
				video: this.videoEl,
				onCrash: (err) => this.handleSubtitleCrash(err)
			});
			try {
				if (typeof window !== 'undefined' && window.localStorage) {
					const saved = window.localStorage.getItem(`hearth_sub_offset_${this.itemId}`);
					if (saved) {
						const val = Number.parseFloat(saved);
						if (!Number.isNaN(val)) {
							this.currentSubOffset = val;
							this.subtitleManager.setOffset(val);
						}
					}
				}
			} catch {
				/* ignore localStorage errors */
			}
		}
		this.subtitleManager.setMediaStartSeconds(response.start_time_seconds ?? 0);

		const source = response.mode === 'direct_play' ? response.url : response.hls_url;
		if (!source) {
			this.loadError = 'The server returned no playable source.';
			this.loading = false;
			return;
		}

		this.expectedDuration = response.file?.duration_seconds ?? 0;
		const startPositionSeconds = this.resumePositionMs > 0 ? this.resumePositionMs / 1000 : 0;

		const callbacks: MediaAdapterCallbacks = {
			onReady: () => {
				this.subtitleManager?.resize();
			},
			onDurationChange: (dur) => {
				this.rawDuration = dur;
				this.updateBufferedRanges();
			},
			onTimeUpdate: (rawTime) => {
				this.currentTime = this.displayTime(rawTime);
				const now = Date.now();
				this.nowMs = now;
				if (now - this.lastReportWallMs >= REPORT_INTERVAL_MS) {
					this.lastReportWallMs = now;
					void this.report('playing');
				}
				if (this.videoEl && (this.videoEl.paused || !('requestVideoFrameCallback' in this.videoEl))) {
					this.subtitleManager?.renderManualFrame(false);
				}
			},
			onBuffering: (waiting) => {
				this.isWaiting = waiting;
			},
			onPlayStateChange: (paused) => {
				this.isPaused = paused;
				this.nowMs = Date.now();
				if (paused) {
					this.subtitleManager?.renderManualFrame(true);
					void this.report('paused');
				}
			},
			onSeeked: () => {
				this.nowMs = Date.now();
				this.subtitleManager?.renderManualFrame(true);
			},
			onEnded: () => {
				void this.report('stopped');
				this.onEnded?.();
			},
			onProgress: () => this.updateBufferedRanges(),
			onStats: (stats) => {
				this.streamStats = stats;
			},
			onError: (msg) => {
				this.loadError = msg;
			}
		};

		const isHls = response.mode !== 'direct_play' || source.includes('.m3u8');
		if (isHls) {
			this.adapter = new HlsAdapter({
				url: source,
				sessionId: this.sessionId,
				isRemux: response.mode === 'remux_hls' || response.plan?.video_action === 'copy',
				startPositionSeconds,
				maxBitrate: response.video_stream?.bit_rate ?? undefined,
				debug: this.debug,
				callbacks
			});
		} else {
			this.adapter = new DirectPlayAdapter({
				url: source,
				startPositionSeconds,
				callbacks
			});
		}

		this.adapter.attach(this.videoEl);
		this.adapter.setPlaybackRate(this.playbackRate);
		void this.applySubtitleChoice();

		if (this.destroyed) return;
		this.loading = false;
		void this.adapter.play().catch(() => {
			/* Autoplay might be blocked by browser policy */
		});
	}

	private embeddedSubtitleUrl(index: number) {
		return (this.info?.embedded_subtitle_urls ?? []).find((entry) => entry.index === index) ?? null;
	}

	private sidecarRequiresBurnIn(
		id: string,
		playbackInfo: PlaybackInfoResponse | null = this.info
	): boolean {
		if (!playbackInfo?.burn_in_complex_subtitles) return false;
		const track = (playbackInfo.subtitles ?? []).find((t) => t.id === id);
		if (!track) return false;
		const fmt = track.format.toLowerCase();
		return fmt === 'ass' || fmt === 'ssa';
	}

	private handleSubtitleCrash(error: unknown): void {
		console.warn('[PlaybackController] Subtitle engine crashed, recovering:', error);

		if (this.subtitleChoice.startsWith('emb:')) {
			const index = Number.parseInt(this.subtitleChoice.slice('emb:'.length), 10);
			const entry = this.embeddedSubtitleUrl(index);
			const rawUrl = entry?.url ?? this.info?.embedded_subtitle_url;

			// 3-step ladder: JASSUB -> Native WebVTT -> Server Burn-In
			if (rawUrl && !this.triedVttFallbackFor.has(this.subtitleChoice) && this.subtitleManager) {
				this.triedVttFallbackFor.add(this.subtitleChoice);
				const vttUrl = rawUrl.replace(/\.ass(\?|$)/i, '.vtt$1');
				const stream = (this.info?.embedded_subtitles ?? []).find((s) => s.index === index);
				void this.subtitleManager.loadTrack(
					{
						id: this.subtitleChoice,
						url: vttUrl,
						format: 'vtt',
						language: stream?.language,
						label: stream?.title || stream?.language || 'Subtitles',
						isContainerAligned: true
					},
					[]
				);
				return;
			}

			this.forceBurnIn = true;
			void this.startPlayback({ forceBurnIn: true });
			return;
		}

		const externalId = this.subtitleChoice.startsWith('ext:')
			? this.subtitleChoice.slice('ext:'.length)
			: null;
		const track = externalId ? (this.info?.subtitles ?? []).find((t) => t.id === externalId) : null;
		if (!track || !this.subtitleManager) return;

		void this.subtitleManager.loadTrack(
			{
				id: track.id,
				url: track.url,
				format: 'vtt',
				language: track.language,
				label: track.name || track.language || 'Subtitles'
			},
			[]
		);
	}

	public async applySubtitleChoice(): Promise<void> {
		if (!this.subtitleManager) return;
		if (this.info?.plan?.subtitle_action === 'burn_in') {
			await this.subtitleManager.clearTrack();
			return;
		}

		const fontUrls = attachmentFontUrls(this.info?.attachments);

		if (this.subtitleChoice.startsWith('emb:')) {
			const index = Number.parseInt(this.subtitleChoice.slice('emb:'.length), 10);
			const entry = this.embeddedSubtitleUrl(index);
			const url = entry?.url ?? this.info?.embedded_subtitle_url;
			if (!url) {
				await this.subtitleManager.clearTrack();
				return;
			}
			const stream = (this.info?.embedded_subtitles ?? []).find((s) => s.index === index);
			await this.subtitleManager.loadTrack(
				{
					id: this.subtitleChoice,
					url,
					format: entry?.format ?? this.info?.embedded_subtitle_format,
					language: stream?.language,
					label: stream?.title || stream?.language || 'Subtitles',
					isContainerAligned: true
				},
				fontUrls
			);
			return;
		}

		const externalId = this.subtitleChoice.startsWith('ext:')
			? this.subtitleChoice.slice('ext:'.length)
			: null;
		if (!externalId) {
			await this.subtitleManager.clearTrack();
			return;
		}

		const selectedTrack = (this.info?.subtitles ?? []).find((t) => t.id === externalId);
		if (!selectedTrack) {
			await this.subtitleManager.clearTrack();
			return;
		}

		await this.subtitleManager.loadTrack(
			{
				id: selectedTrack.id,
				url: selectedTrack.url,
				format: selectedTrack.format,
				language: selectedTrack.language,
				label: selectedTrack.name || selectedTrack.language || 'Subtitles'
			},
			fontUrls
		);
	}

	public selectAudioTrack(value: string): void {
		const next = value === '' ? null : Number.parseInt(value, 10);
		if (next === this.audioTrackIndex) return;
		this.audioTrackIndex = next;
		void this.startPlayback();
	}

	public selectSubtitle(value: string): void {
		this.subtitleChoice = value;

		if (this.info?.plan?.subtitle_action === 'burn_in') {
			this.forceBurnIn = false;
			void this.startPlayback();
			return;
		}

		if (value.startsWith('emb:')) {
			const index = Number.parseInt(value.slice('emb:'.length), 10);
			if (!this.embeddedSubtitleUrl(index)) {
				void this.startPlayback();
				return;
			}
		} else if (value.startsWith('ext:') && this.sidecarRequiresBurnIn(value.slice('ext:'.length))) {
			void this.startPlayback();
			return;
		}

		void this.applySubtitleChoice();
	}

	public selectQuality(value: QualityLevel): void {
		if (value === this.quality) return;
		this.quality = value;
		void this.startPlayback();
	}

	public setPlaybackRate(rate: number): void {
		this.playbackRate = rate;
		this.adapter?.setPlaybackRate(rate);
	}

	public setSubOffset(offsetSeconds: number): void {
		this.currentSubOffset = offsetSeconds;
		this.subtitleManager?.setOffset(offsetSeconds);
	}

	public togglePlay(): void {
		if (!this.videoEl || !this.adapter) return;
		if (this.videoEl.paused) {
			void this.adapter.play().catch(() => {});
		} else {
			this.adapter.pause();
		}
	}

	public seek(contentTargetSeconds: number): void {
		const maxTime = this.duration > 0 ? this.duration : Number.POSITIVE_INFINITY;
		const nextTime = Math.max(0, Math.min(maxTime, contentTargetSeconds));
		const fileTime = nextTime + this.mediaStartSec;
		this.currentTime = nextTime;
		void this.adapter?.seek(fileTime);
		this.subtitleManager?.renderManualFrame(true);
	}

	public seekBy(deltaSeconds: number): void {
		if (!this.videoEl) return;
		const cur = this.displayTime(this.videoEl.currentTime ?? 0);
		this.seek(cur + deltaSeconds);
	}

	public toggleMute(): void {
		if (!this.videoEl) return;
		this.videoEl.muted = !this.videoEl.muted;
		this.isMuted = this.videoEl.muted;
	}

	public setVolume(newVol: number): void {
		if (!this.videoEl) return;
		this.volume = Math.max(0, Math.min(1, newVol));
		this.videoEl.volume = this.volume;
		if (this.volume > 0 && this.isMuted) {
			this.videoEl.muted = false;
			this.isMuted = false;
		}
	}

	public updateBufferedRanges(): void {
		if (!this.videoEl || this.duration <= 0) {
			this.bufferedRanges = [];
			return;
		}
		const buf = this.videoEl.buffered;
		const ranges: { startPct: number; widthPct: number }[] = [];
		for (let i = 0; i < buf.length; i++) {
			const start = this.displayTime(buf.start(i));
			const end = this.displayTime(buf.end(i));
			const startPct = Math.max(0, Math.min(100, (start / this.duration) * 100));
			const endPct = Math.max(0, Math.min(100, (end / this.duration) * 100));
			ranges.push({
				startPct,
				widthPct: Math.max(0, endPct - startPct)
			});
		}
		this.bufferedRanges = ranges;
	}

	public resizeSubtitles(): void {
		this.subtitleManager?.resize();
	}

	public async report(state: 'playing' | 'paused' | 'stopped'): Promise<void> {
		if (!this.videoEl || this.destroyed) return;
		const cur = this.videoEl.currentTime ?? 0;
		const dur = this.mediaDurationRaw;
		const position_ms = Math.max(0, Math.floor(cur * 1000));
		const duration_ms = dur > 0 ? Math.floor(dur * 1000) : undefined;
		this.lastReportWallMs = Date.now();
		try {
			await api.post(`/items/${this.itemId}/progress`, {
				body: { position_ms, duration_ms, state }
			});
		} catch {
			/* progress is best-effort */
		}
	}

	public sendBeaconStop(): void {
		if (this.sessionId) {
			sendStopBeacon(this.sessionId, this.sessionMediaToken);
		}
	}

	public destroy(): void {
		this.destroyed = true;
		++this.startGeneration;
		void this.report('stopped');
		this.stopActiveSession();
		this.adapter?.destroy();
		this.adapter = null;
		void this.subtitleManager?.destroy();
		this.subtitleManager = null;
		this.videoEl = null;
	}
}
