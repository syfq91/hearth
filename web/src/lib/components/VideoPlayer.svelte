<script lang="ts">
	import {
		ArrowLeft,
		Play,
		Pause,
		RotateCcw,
		RotateCw,
		Volume2,
		Volume1,
		VolumeX,
		Maximize,
		Minimize,
		PictureInPicture2,
		SlidersHorizontal,
		Info,
		Languages,
		Captions,
		Gauge
	} from '@lucide/svelte';
	import { api } from '../api/client';
	import { detectBrowserCapabilities, getCapabilitiesWithBurnInFallback } from '../capabilities';
	import { sendStopBeacon } from '../video-resilience';
	import {
		HlsEngine,
		isHlsSupported,
		isNativeHlsSupported,
		type HlsQualityLevel,
		type HlsStreamStats
	} from '../player/hls-engine';
	import { SubtitleManager } from '../player/subtitles.svelte';
	import { attachmentFontUrls } from '../video-subtitles';
	import SubtitleSyncControl from './SubtitleSyncControl.svelte';
	import type { PlaybackInfoResponse } from '../api/types';
	import { session } from '../auth.svelte';
	import { resolveAutoSubtitle } from '../languages';

	interface Props {
		itemId: string;
		/** Resume point in milliseconds, from the item's watch state. */
		startAtMs?: number;
		title: string;
		/** Fired once when playback reaches the end of the item. */
		onEnded?: () => void;
	}

	let { itemId, startAtMs = 0, title, onEnded }: Props = $props();

	let containerEl = $state<HTMLDivElement | null>(null);
	let videoEl = $state<HTMLVideoElement | null>(null);
	let seekbarEl = $state<HTMLDivElement | null>(null);

	let hlsEngine: HlsEngine | null = null;
	let subtitleManager = $state<SubtitleManager | null>(null);

	let info = $state<PlaybackInfoResponse | null>(null);
	let loadError = $state<string | null>(null);
	let loading = $state(true);
	let isWaiting = $state(false);

	let audioTrackIndex = $state<number | null>(null);
	let subtitleChoice = $state<string>('');
	let embeddedSubtitleIndex = $state<number | null>(null);
	let forceBurnIn = $state(false);

	// Subtitle sync modal
	let showSubSync = $state(false);
	let currentSubOffset = $state(0);

	// Timing and duration
	let currentTime = $state(0);
	let rawDuration = $state(0);
	let expectedDuration = $state(0);
	let bufferedRanges = $state<{ startPct: number; widthPct: number }[]>([]);

	let isPaused = $state(true);
	let volume = $state(1);
	let isMuted = $state(false);
	let playbackRate = $state(1);

	// Rendition quality levels for HLS
	let levels = $state<HlsQualityLevel[]>([]);
	let qualityLevel = $state(-1); // -1 = Auto ABR
	let streamStats = $state<HlsStreamStats | null>(null);

	// Wall clock, ticked so the projected end time stays truthful while paused.
	let nowMs = $state(Date.now());

	// OSD activity state
	let isUserActive = $state(true);
	let hideTimeout: ReturnType<typeof setTimeout> | null = null;
	let showModeBadge = $state(false);
	let openMenu = $state<'audio' | 'subtitle' | 'speed' | null>(null);

	// Fullscreen & PiP states
	let isFullscreen = $state(false);
	let isPip = $state(false);

	// Seekbar hover state
	let isHoveringSeekbar = $state(false);
	let hoverTime = $state(0);
	let hoverPct = $state(0);

	// Session management
	let sessionId: string | null = null;
	let sessionMediaToken: string | null = null;
	let resumeApplied = false;
	let resumePositionMs = 0;
	let lastReportedMs = 0;
	let destroyed = false;
	let focusTaken = false;

	let lastItemId = '';
	let lastAudioTrack: number | null = null;
	let lastSubChoice = '';

	const REPORT_INTERVAL_MS = 10_000;
	const HIDE_DELAY_MS = 1500;
	const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 2] as const;

	const duration = $derived.by(() => {
		if (rawDuration > 0 && Number.isFinite(rawDuration)) return rawDuration;
		if (expectedDuration > 0) return expectedDuration;
		return 0;
	});

	const progressPct = $derived.by(() => {
		if (duration <= 0) return 0;
		return Math.min(100, Math.max(0, (currentTime / duration) * 100));
	});

	function scheduleHide(): void {
		if (hideTimeout) clearTimeout(hideTimeout);
		hideTimeout = null;
		if (isPaused) return;
		hideTimeout = setTimeout(() => {
			isUserActive = false;
			openMenu = null;
		}, HIDE_DELAY_MS);
	}

	function triggerActivity(): void {
		isUserActive = true;
		scheduleHide();
	}

	function toggleControls(): void {
		if (isPaused) {
			isUserActive = true;
			openMenu = null;
			return;
		}
		if (isUserActive) {
			isUserActive = false;
			openMenu = null;
			if (hideTimeout) clearTimeout(hideTimeout);
			hideTimeout = null;
			return;
		}
		triggerActivity();
	}

	function toggleMenu(menu: 'audio' | 'subtitle' | 'speed'): void {
		openMenu = openMenu === menu ? null : menu;
		triggerActivity();
	}

	function formatTime(seconds: number): string {
		if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
		const totalSecs = Math.floor(seconds);
		const hrs = Math.floor(totalSecs / 3600);
		const mins = Math.floor((totalSecs % 3600) / 60);
		const secs = totalSecs % 60;
		const paddedSecs = secs.toString().padStart(2, '0');
		if (hrs > 0) {
			return `${hrs}:${mins.toString().padStart(2, '0')}:${paddedSecs}`;
		}
		return `${mins}:${paddedSecs}`;
	}

	function formatBitrate(bitsPerSecond: number | undefined): string {
		if (!bitsPerSecond) return 'unknown bitrate';
		const mbps = bitsPerSecond / 1_000_000;
		return mbps >= 1 ? `${mbps.toFixed(1)} Mbps` : `${Math.round(bitsPerSecond / 1000)} kbps`;
	}

	/** Nominal bitrate of what is actually being delivered to the player. */
	const streamBitrate = $derived.by(() => {
		if (streamStats?.levelBitrate) return streamStats.levelBitrate;
		const planBitrate = info?.plan?.target_bitrate;
		if (planBitrate) return planBitrate;
		const videoBitrate = info?.video_stream?.bit_rate ?? 0;
		const audioBitrate = activeAudioStream()?.bit_rate ?? 0;
		const probed = videoBitrate + audioBitrate;
		if (probed > 0) return probed;
		// Direct play often probes no per-stream bitrate; derive it from the file.
		const sizeBytes = info?.file?.size_bytes ?? 0;
		const durationSecs = info?.file?.duration_seconds ?? 0;
		if (sizeBytes > 0 && durationSecs > 0) return (sizeBytes * 8) / durationSecs;
		return 0;
	});

	/** Throughput actually achieved fetching HLS segments. */
	const streamThroughput = $derived(streamStats?.measuredBitrate ?? 0);

	/** Wall-clock time playback will finish at the current rate and position. */
	const endsAt = $derived.by(() => {
		if (duration <= 0) return '';
		const rate = playbackRate > 0 ? playbackRate : 1;
		const remainingSecs = Math.max(0, duration - currentTime) / rate;
		const finish = new Date(nowMs + remainingSecs * 1000);
		return finish.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
	});

	async function report(state: 'playing' | 'paused' | 'stopped'): Promise<void> {
		if (!videoEl || destroyed) return;
		const cur = videoEl.currentTime ?? 0;
		const dur = duration;
		const position_ms = Math.max(0, Math.floor(cur * 1000));
		const duration_ms = dur > 0 ? Math.floor(dur * 1000) : undefined;
		lastReportedMs = Math.max(lastReportedMs, position_ms);
		try {
			await api.post(`/items/${itemId}/progress`, {
				body: { position_ms, duration_ms, state }
			});
		} catch {
			/* progress is best-effort */
		}
	}

	function updateBufferedRanges(): void {
		if (!videoEl || duration <= 0) {
			bufferedRanges = [];
			return;
		}
		const buf = videoEl.buffered;
		const ranges: { startPct: number; widthPct: number }[] = [];
		for (let i = 0; i < buf.length; i++) {
			const start = buf.start(i);
			const end = buf.end(i);
			const startPct = Math.max(0, Math.min(100, (start / duration) * 100));
			const endPct = Math.max(0, Math.min(100, (end / duration) * 100));
			ranges.push({
				startPct,
				widthPct: Math.max(0, endPct - startPct)
			});
		}
		bufferedRanges = ranges;
	}

	function applyResume(): void {
		if (resumeApplied || !videoEl) return;
		const targetMs = resumePositionMs > 0 ? resumePositionMs : startAtMs;
		if (targetMs <= 0) return;
		resumeApplied = true;
		const dur = duration;
		const durationMs = dur * 1000;
		// Near the end the user wants a rewatch prompt, not a jump to credits.
		// Only skip when the position came from the server resume point. By this
		// point resumePositionMs is always positive (startPlayback copies
		// startAtMs into it), so checking it here made this guard unreachable.
		if (startAtMs > 0 && Number.isFinite(durationMs) && durationMs > 30_000 && durationMs - targetMs < 30_000) {
			return;
		}
		videoEl.currentTime = targetMs / 1000;
		currentTime = targetMs / 1000;
	}

	function togglePlay(): void {
		if (!videoEl) return;
		if (videoEl.paused) {
			void videoEl.play().catch(() => {});
		} else {
			videoEl.pause();
		}
	}

	function seekBy(deltaSeconds: number): void {
		if (!videoEl) return;
		const cur = videoEl.currentTime ?? 0;
		const maxTime = duration > 0 ? duration : Number.POSITIVE_INFINITY;
		const nextTime = Math.max(0, Math.min(maxTime, cur + deltaSeconds));
		videoEl.currentTime = nextTime;
		currentTime = nextTime;
		triggerActivity();
		subtitleManager?.renderManualFrame(true);
	}

	function handleSeek(targetSeconds: number): void {
		if (!videoEl) return;
		const maxTime = duration > 0 ? duration : Number.POSITIVE_INFINITY;
		const nextTime = Math.max(0, Math.min(maxTime, targetSeconds));
		videoEl.currentTime = nextTime;
		currentTime = nextTime;
		subtitleManager?.renderManualFrame(true);
	}

	function handleSeekbarClick(e: MouseEvent): void {
		if (!seekbarEl || duration <= 0) return;
		const rect = seekbarEl.getBoundingClientRect();
		const clickX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
		const pct = clickX / rect.width;
		handleSeek(pct * duration);
	}

	function handleSeekbarMouseMove(e: MouseEvent): void {
		if (!seekbarEl || duration <= 0) return;
		const rect = seekbarEl.getBoundingClientRect();
		const hoverX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
		hoverPct = (hoverX / rect.width) * 100;
		hoverTime = (hoverX / rect.width) * duration;
	}

	function toggleMute(): void {
		if (!videoEl) return;
		videoEl.muted = !videoEl.muted;
		isMuted = videoEl.muted;
	}

	function handleVolumeInput(newVol: number): void {
		if (!videoEl) return;
		volume = Math.max(0, Math.min(1, newVol));
		videoEl.volume = volume;
		if (volume > 0 && isMuted) {
			videoEl.muted = false;
			isMuted = false;
		}
	}

	function handlePlaybackRateChange(newRate: number): void {
		if (!videoEl) return;
		playbackRate = newRate;
		videoEl.playbackRate = newRate;
	}

	function toggleFullscreen(): void {
		if (!containerEl) return;
		if (document.fullscreenElement) {
			void document.exitFullscreen().catch(() => {});
		} else {
			void containerEl.requestFullscreen().catch(() => {});
		}
	}

	function togglePip(): void {
		if (!videoEl) return;
		if (document.pictureInPictureElement) {
			void document.exitPictureInPicture().catch(() => {});
		} else if (document.pictureInPictureEnabled && videoEl.requestPictureInPicture) {
			void videoEl.requestPictureInPicture().catch(() => {});
		}
	}

	function handleKeydown(event: KeyboardEvent): void {
		// Ignore keys starting inside text/number inputs
		const targetTag = (event.target as HTMLElement)?.tagName?.toLowerCase();
		if (targetTag === 'input' && (event.target as HTMLInputElement).type !== 'range') {
			return;
		}

		switch (event.key) {
			case ' ':
			case 'k':
				event.preventDefault();
				togglePlay();
				break;
			case 'ArrowRight':
			case 'l':
				event.preventDefault();
				seekBy(10);
				break;
			case 'ArrowLeft':
			case 'j':
				event.preventDefault();
				seekBy(-10);
				break;
			case 'ArrowUp':
				event.preventDefault();
				handleVolumeInput(volume + 0.05);
				break;
			case 'ArrowDown':
				event.preventDefault();
				handleVolumeInput(volume - 0.05);
				break;
			case 'm':
				event.preventDefault();
				toggleMute();
				break;
			case 'f':
				event.preventDefault();
				toggleFullscreen();
				break;
			case 'p':
				event.preventDefault();
				togglePip();
				break;
			case 's':
				event.preventDefault();
				showSubSync = !showSubSync;
				break;
			case 'i':
				event.preventDefault();
				showModeBadge = !showModeBadge;
				break;
			case 'Escape':
				if (openMenu) {
					event.preventDefault();
					openMenu = null;
				}
				break;
			default:
				break;
		}
	}

	function handleSubtitleCrash(error: unknown): void {
		console.warn('[VideoPlayer] Subtitle engine crashed, recovering:', error);

		if (subtitleChoice.startsWith('emb:')) {
			// Embedded streams can be rendered server-side by FFmpeg.
			forceBurnIn = true;
			void startPlayback({ forceBurnIn: true });
			return;
		}

		// External sidecars cannot be burned in. Fall back to the server-side
		// WebVTT conversion and render it as a native text track.
		const externalId = subtitleChoice.startsWith('ext:')
			? subtitleChoice.slice('ext:'.length)
			: null;
		const track = externalId
			? (info?.subtitles ?? []).find((t) => t.id === externalId)
			: null;
		if (!track || !subtitleManager) return;

		void subtitleManager.loadTrack(
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

	function embeddedSubtitleUrl(index: number) {
		return (info?.embedded_subtitle_urls ?? []).find((entry) => entry.index === index) ?? null;
	}

	async function applySubtitleChoice(): Promise<void> {
		if (!subtitleManager) return;

		// Font attachment URLs already carry the media token for both direct
		// play and HLS, so reuse them instead of rebuilding the URL from
		// sessionMediaToken (which is only populated from an HLS URL).
		const fontUrls = attachmentFontUrls(info?.attachments);

		if (subtitleChoice.startsWith('emb:')) {
			const index = Number.parseInt(subtitleChoice.slice('emb:'.length), 10);
			const entry = embeddedSubtitleUrl(index);
			// Bitmap tracks have no client-renderable URL; they need burn-in.
			const url = entry?.url ?? info?.embedded_subtitle_url;
			if (!url) {
				await subtitleManager.clearTrack();
				return;
			}
			const stream = (info?.embedded_subtitles ?? []).find((s) => s.index === index);
			await subtitleManager.loadTrack(
				{
					id: subtitleChoice,
					url,
					format: entry?.format ?? info?.embedded_subtitle_format,
					language: stream?.language,
					label: stream?.title || stream?.language || 'Subtitles'
				},
				fontUrls
			);
			return;
		}

		const externalId = subtitleChoice.startsWith('ext:')
			? subtitleChoice.slice('ext:'.length)
			: null;

		if (!externalId) {
			await subtitleManager.clearTrack();
			return;
		}

		const selectedTrack = (info?.subtitles ?? []).find((t) => t.id === externalId);
		if (!selectedTrack) {
			await subtitleManager.clearTrack();
			return;
		}

		await subtitleManager.loadTrack(
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

	async function startPlayback(options: { forceBurnIn?: boolean } = {}): Promise<void> {
		destroyed = false;
		loading = true;
		loadError = null;
		expectedDuration = 0;

		if (options.forceBurnIn) {
			forceBurnIn = true;
		}

		const previousSession = sessionId;
		const previousToken = sessionMediaToken;
		sessionId = null;
		sessionMediaToken = null;
		if (previousSession) {
			sendStopBeacon(previousSession, previousToken);
		}

		embeddedSubtitleIndex = subtitleChoice.startsWith('emb:')
			? Number.parseInt(subtitleChoice.slice('emb:'.length), 10)
			: null;

		const cur = videoEl?.currentTime ?? 0;
		if (cur > 0) {
			resumePositionMs = Math.floor(cur * 1000);
		} else if (startAtMs > 0) {
			resumePositionMs = startAtMs;
		}
		const offsetSeconds = resumePositionMs > 0 ? resumePositionMs / 1000 : 0;

		const clientCapabilities = forceBurnIn
			? getCapabilitiesWithBurnInFallback()
			: detectBrowserCapabilities();

		let response: PlaybackInfoResponse;
		try {
			response = await api.post<PlaybackInfoResponse>(`/items/${itemId}/playback-info`, {
				body: {
					capabilities: clientCapabilities,
					audio_track_index: audioTrackIndex,
					subtitle_track_index: embeddedSubtitleIndex,
					start_time_offset_seconds: offsetSeconds,
					force_burn_in_subtitles: forceBurnIn
				}
			});
		} catch (e) {
			loadError = e instanceof Error ? e.message : String(e);
			loading = false;
			return;
		}

		if (destroyed) return;
		info = response;
		if (audioTrackIndex === null && response.plan?.audio_stream_index != null) {
			audioTrackIndex = response.plan.audio_stream_index;
			lastAudioTrack = audioTrackIndex;
		}
		if (subtitleChoice === '') {
			const activeAudio =
				(response.audio_streams ?? []).find((s) => s.index === audioTrackIndex) ??
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
				subtitleChoice = autoSub;
				lastSubChoice = autoSub;
			}
		}
		sessionId = response.session_id ?? null;
		if (response.hls_url) {
			try {
				const parsed = new URL(response.hls_url, window.location.origin);
				sessionMediaToken = parsed.searchParams.get('token');
			} catch {
				sessionMediaToken = null;
			}
		}

		await attachSource(response);
	}

	async function attachSource(response: PlaybackInfoResponse): Promise<void> {
		if (!videoEl) return;
		resumeApplied = false;
		lastReportedMs = 0;
		levels = [];
		qualityLevel = -1;
		streamStats = null;

		if (hlsEngine) {
			hlsEngine.destroy();
			hlsEngine = null;
		}

		if (!subtitleManager) {
			subtitleManager = new SubtitleManager({
				video: videoEl,
				onCrash: handleSubtitleCrash
			});

			// Restore saved user offset from localStorage
			try {
				if (typeof window !== 'undefined' && window.localStorage) {
					const saved = window.localStorage.getItem(`hearth_sub_offset_${itemId}`);
					if (saved) {
						const val = Number.parseFloat(saved);
						if (!Number.isNaN(val)) {
							currentSubOffset = val;
							subtitleManager.setOffset(val);
						}
					}
				}
			} catch {
				/* ignore localStorage errors */
			}
		}

		const source = response.mode === 'direct_play' ? response.url : response.hls_url;
		if (!source) {
			loadError = 'The server returned no playable source.';
			loading = false;
			return;
		}

		expectedDuration = response.file?.duration_seconds ?? 0;

		const isHls = response.mode !== 'direct_play' || source.includes('.m3u8');
		if (isHls && !isNativeHlsSupported(videoEl) && isHlsSupported()) {
			hlsEngine = new HlsEngine(videoEl, source, {
				startPositionSeconds: resumePositionMs > 0 ? resumePositionMs / 1000 : -1,
				maxBitrate: response.video_stream?.bit_rate ?? undefined,
				onLevelLoaded: (loadedLevels) => {
					levels = loadedLevels;
				},
				onStats: (stats) => {
					streamStats = stats;
				},
				onError: (err) => {
					console.error('[VideoPlayer] HLS Fatal Error:', err);
					loadError = `Playback error: ${err.details || 'stream failure'}`;
				}
			});
		} else {
			videoEl.src = source;
		}

		loading = false;
		void videoEl.play().catch(() => {
			/* Autoplay might be blocked by browser policy */
		});

		void applySubtitleChoice();
	}

	function selectAudioTrack(value: string): void {
		audioTrackIndex = value === '' ? null : Number.parseInt(value, 10);
		openMenu = null;
	}

	function selectSubtitle(value: string): void {
		subtitleChoice = value;
		openMenu = null;

		// Burned-in cues are part of the video stream; only a fresh session
		// without subtitles can remove them.
		if (info?.plan?.subtitle_action === 'burn_in') {
			void startPlayback();
			return;
		}

		// Bitmap subtitle tracks cannot be rendered by the client.
		if (value.startsWith('emb:')) {
			const index = Number.parseInt(value.slice('emb:'.length), 10);
			if (!embeddedSubtitleUrl(index)) {
				void startPlayback();
				return;
			}
		}

		void applySubtitleChoice();
	}

	function selectPlaybackRate(rate: number): void {
		handlePlaybackRateChange(rate);
		openMenu = null;
	}

	function handleQualityChange(event: Event): void {
		const val = Number.parseInt((event.currentTarget as HTMLSelectElement).value, 10);
		qualityLevel = val;
		hlsEngine?.setQualityLevel(val);
	}

	function handleOffsetSyncChange(offsetSeconds: number): void {
		currentSubOffset = offsetSeconds;
		subtitleManager?.setOffset(offsetSeconds);
	}

	function codecLabel(name: string | null | undefined): string {
		const raw = name ?? 'unknown';
		if (raw === 'hevc') return 'x265';
		if (raw === 'h264') return 'x264';
		return raw;
	}

	function targetVideoLabel(codec: string | undefined): string {
		switch (codec) {
			case 'libx264':
				return 'x264';
			case 'h264_vaapi':
				return 'vaapi x264';
			case 'hevc_vaapi':
				return 'vaapi x265';
			case 'h264_qsv':
				return 'qsv x264';
			case 'hevc_qsv':
				return 'qsv x265';
			default:
				return codec ?? 'transcode';
		}
	}

	function activeAudioStream() {
		if (!info) return undefined;
		return (
			(audioTrackIndex !== null
				? (info.audio_streams ?? []).find((s) => s.index === audioTrackIndex)
				: null) ??
			(info.audio_streams ?? []).find((s) => s.is_default) ??
			info.audio_streams?.[0]
		);
	}

	function playbackModeDetail(): string {
		if (!info) return '';
		if (info.mode === 'direct_play') return 'Direct play';
		if (info.mode === 'remux_hls') return 'Remux';
		return 'Transcoding';
	}

	function videoStreamDetail(): string {
		if (!info) return '';
		const src = codecLabel(info.video_stream?.codec_name);
		if (info.mode === 'direct_play') return src;
		const plan = info.plan;
		if ((plan?.video_action ?? 'transcode') === 'copy') return `${src} > copy`;
		return `${src} > ${targetVideoLabel(plan?.video_codec)}`;
	}

	function audioStreamDetail(): string {
		if (!info) return '';
		const src = codecLabel(activeAudioStream()?.codec_name ?? 'audio');
		if (info.mode === 'direct_play') return src;
		const plan = info.plan;
		if ((plan?.audio_action ?? 'transcode') === 'copy') return `${src} > copy`;
		return `${src} > ${plan?.audio_codec ?? 'aac'}`;
	}

	$effect(() => {
		if (videoEl && !info && !loadError) {
			lastItemId = itemId;
			lastAudioTrack = audioTrackIndex;
			lastSubChoice = subtitleChoice;
			void startPlayback();
		}
	});

	$effect(() => {
		const currentItem = itemId;
		const currentAudio = audioTrackIndex;

		if (!videoEl || loading) return;

		if (currentItem !== lastItemId) {
			lastItemId = currentItem;
			lastAudioTrack = null;
			audioTrackIndex = null;
			lastSubChoice = '';
			subtitleChoice = '';
			resumePositionMs = 0;
			info = null;
			forceBurnIn = false;
			void startPlayback();
		} else if (currentAudio !== lastAudioTrack) {
			lastAudioTrack = currentAudio;
			void startPlayback();
		}
	});

	// Keyboard focus on mount
	$effect(() => {
		const el = videoEl;
		if (!el || focusTaken) return;
		focusTaken = true;
		el.focus({ preventScroll: true });
	});

	// Fullscreen & PiP change listeners
	$effect(() => {
		const handleFsChange = () => {
			isFullscreen = Boolean(document.fullscreenElement);
			subtitleManager?.resize();
		};
		const el = videoEl;
		const onEnterPip = () => {
			isPip = true;
		};
		const onLeavePip = () => {
			isPip = false;
		};
		document.addEventListener('fullscreenchange', handleFsChange);
		if (el) {
			el.addEventListener('enterpictureinpicture', onEnterPip);
			el.addEventListener('leavepictureinpicture', onLeavePip);
		}
		return () => {
			document.removeEventListener('fullscreenchange', handleFsChange);
			if (el) {
				el.removeEventListener('enterpictureinpicture', onEnterPip);
				el.removeEventListener('leavepictureinpicture', onLeavePip);
			}
		};
	});

	// Page unload & visibility reporting
	$effect(() => {
		const onHide = () => {
			void report('paused');
			if (sessionId) {
				sendStopBeacon(sessionId, sessionMediaToken);
			}
		};
		window.addEventListener('pagehide', onHide);
		document.addEventListener('visibilitychange', onHide);
		return () => {
			window.removeEventListener('pagehide', onHide);
			document.removeEventListener('visibilitychange', onHide);
		};
	});

	// Teardown
	$effect(() => {
		return () => {
			if (hideTimeout) clearTimeout(hideTimeout);
			destroyed = true;
			void report('stopped');
			if (sessionId) {
				sendStopBeacon(sessionId, sessionMediaToken);
			}
			hlsEngine?.destroy();
			hlsEngine = null;
			void subtitleManager?.destroy();
			subtitleManager = null;
		};
	});

	const availableAudio = $derived(info?.audio_streams ?? []);
	const availableSubtitles = $derived(info?.subtitles ?? []);
	const availableEmbeddedSubtitles = $derived(info?.embedded_subtitles ?? []);
</script>

<svelte:window
	onpointermove={triggerActivity}
	onkeydown={(e) => {
		triggerActivity();
		handleKeydown(e);
	}}
/>

<div
	class="player"
	class:user-inactive={!isPaused && !isUserActive && !loading && !loadError}
	bind:this={containerEl}
>
	{#if loadError}
		<p class="banner" role="alert">{loadError}</p>
	{:else if loading}
		<p class="status" role="status">Preparing playback…</p>
	{/if}

	{#if isWaiting && !loading && !loadError}
		<div class="buffering-spinner" role="status" aria-label="Buffering"></div>
	{/if}

	<div class="video-container" class:has-jassub={subtitleManager?.isAss}>
		<!-- svelte-ignore a11y_media_has_caption -->
		<video
			bind:this={videoEl}
			tabindex="0"
			playsinline
			preload="metadata"
			style="object-fit: contain;"
			onplay={() => {
				isPaused = false;
				nowMs = Date.now();
				triggerActivity();
			}}
			onpause={() => {
				isPaused = true;
				nowMs = Date.now();
				isUserActive = true;
				if (hideTimeout) clearTimeout(hideTimeout);
				subtitleManager?.renderManualFrame(true);
				void report('paused');
			}}
			ontimeupdate={() => {
				if (!videoEl) return;
				currentTime = videoEl.currentTime;
				nowMs = Date.now();
				const positionMs = Math.floor(currentTime * 1000);
				if (positionMs - lastReportedMs >= REPORT_INTERVAL_MS) {
					lastReportedMs = positionMs;
					void report('playing');
				}
				// Reconcile the cue every tick. JASSUB drives itself from
				// requestVideoFrameCallback, but that loop stalls on background
				// tabs, PiP transitions and hls.js media swaps, leaving the
				// last frame on screen.
				subtitleManager?.renderManualFrame(false);
			}}
			onprogress={updateBufferedRanges}
			ondurationchange={() => {
				if (videoEl && Number.isFinite(videoEl.duration) && videoEl.duration > 0) {
					rawDuration = videoEl.duration;
				}
			}}
			onloadedmetadata={() => {
				if (videoEl && Number.isFinite(videoEl.duration) && videoEl.duration > 0) {
					rawDuration = videoEl.duration;
				}
				applyResume();
				subtitleManager?.resize();
			}}
			oncanplay={() => {
				isWaiting = false;
				applyResume();
			}}
			onwaiting={() => {
				isWaiting = true;
			}}
			onplaying={() => {
				isWaiting = false;
			}}
			onseeked={() => {
				nowMs = Date.now();
				subtitleManager?.renderManualFrame(true);
			}}
			onended={() => {
				isPaused = true;
				isUserActive = true;
				if (hideTimeout) clearTimeout(hideTimeout);
				void report('stopped');
				onEnded?.();
			}}
			onerror={() => {
				if (videoEl?.error) {
					loadError = `Playback error: ${videoEl.error.message || 'Media decode failed'}`;
				}
			}}
			onclick={toggleControls}
		></video>
	</div>

	<!-- Top Overlay Navigation Bar -->
	<div
		class="top-overlay"
		class:hidden={!isPaused && !isUserActive && !loading && !loadError}
		onpointerdown={triggerActivity}
		role="navigation"
		aria-label="Player navigation"
	>
		<div class="top-left">
			<a
				href="/"
				class="back-btn"
				onclick={(e) => {
					if (typeof window !== 'undefined' && window.history.length > 1) {
						e.preventDefault();
						window.history.back();
					}
				}}
				aria-label="Home"
				title="Close (Back)"
			>
				<ArrowLeft size={22} />
				<span class="visually-hidden">Home</span>
			</a>
			<h1 class="player-title" title={title}>{title}</h1>
		</div>
	</div>

	<!-- Bottom Overlay Controls Bar -->
	<div
		class="bottom-overlay"
		class:hidden={!isPaused && !isUserActive && !loading && !loadError}
		onpointerdown={triggerActivity}
		role="toolbar"
		aria-label="Player controls"
		tabindex="-1"
	>
		{#if openMenu}
			<div
				class="control-menu"
				role="menu"
				aria-label={openMenu === 'audio'
					? 'Audio tracks'
					: openMenu === 'subtitle'
						? 'Subtitle tracks'
						: 'Playback speed'}
			>
				{#if openMenu === 'audio'}
					<div class="menu-heading" role="presentation">Audio</div>
					<button
						type="button"
						role="menuitemradio"
						aria-checked={audioTrackIndex === null}
						class="menu-item"
						class:selected={audioTrackIndex === null}
						onclick={() => selectAudioTrack('')}
					>
						Default audio
					</button>
					{#each availableAudio as track (track.index)}
						<button
							type="button"
							role="menuitemradio"
							aria-checked={audioTrackIndex === track.index}
							class="menu-item"
							class:selected={audioTrackIndex === track.index}
							onclick={() => selectAudioTrack(String(track.index))}
						>
							{track.title ?? track.language ?? 'Audio'} ({track.codec_name ?? '?'})
						</button>
					{/each}
				{:else if openMenu === 'subtitle'}
					<div class="menu-heading" role="presentation">Subtitles</div>
					<button
						type="button"
						role="menuitemradio"
						aria-checked={subtitleChoice === ''}
						class="menu-item"
						class:selected={subtitleChoice === ''}
						onclick={() => selectSubtitle('')}
					>
						Subtitles off
					</button>
					{#each availableSubtitles as track (track.id)}
						<button
							type="button"
							role="menuitemradio"
							aria-checked={subtitleChoice === `ext:${track.id}`}
							class="menu-item"
							class:selected={subtitleChoice === `ext:${track.id}`}
							onclick={() => selectSubtitle(`ext:${track.id}`)}
						>
							{track.name || track.language || 'Subtitles'} ({track.format})
						</button>
					{/each}
					{#each availableEmbeddedSubtitles as stream (stream.index)}
						<button
							type="button"
							role="menuitemradio"
							aria-checked={subtitleChoice === `emb:${stream.index}`}
							class="menu-item"
							class:selected={subtitleChoice === `emb:${stream.index}`}
							onclick={() => selectSubtitle(`emb:${stream.index}`)}
						>
							{stream.title ?? stream.language ?? 'Subtitle'} (embedded)
						</button>
					{/each}
				{:else}
					<div class="menu-heading" role="presentation">Playback speed</div>
					{#each PLAYBACK_RATES as rate (rate)}
						<button
							type="button"
							role="menuitemradio"
							aria-checked={playbackRate === rate}
							class="menu-item"
							class:selected={playbackRate === rate}
							onclick={() => selectPlaybackRate(rate)}
						>
							{rate}x
						</button>
					{/each}
				{/if}
			</div>
		{/if}

		<!-- Seekbar with elapsed time on the left and total duration on the right -->
		<div class="seek-row">
			<span class="time-current" aria-live="off">{formatTime(currentTime)}</span>
			<div
				class="seekbar-container"
				bind:this={seekbarEl}
				onclick={handleSeekbarClick}
				onmouseenter={() => (isHoveringSeekbar = true)}
				onmouseleave={() => (isHoveringSeekbar = false)}
				onmousemove={handleSeekbarMouseMove}
				role="slider"
				tabindex="0"
				aria-label="Seek timeline"
				aria-valuemin="0"
				aria-valuemax={duration}
				aria-valuenow={currentTime}
				aria-valuetext={formatTime(currentTime)}
				onkeydown={(e) => {
					if (e.key === 'ArrowRight') seekBy(5);
					if (e.key === 'ArrowLeft') seekBy(-5);
				}}
			>
				<div class="seekbar-track">
					{#each bufferedRanges as range}
						<div
							class="buffered-bar"
							style="left: {range.startPct}%; width: {range.widthPct}%;"
						></div>
					{/each}
					<div class="played-bar" style="width: {progressPct}%;"></div>
					<div class="scrub-thumb" style="left: {progressPct}%;"></div>
				</div>

				{#if isHoveringSeekbar && duration > 0}
					<div class="hover-tooltip" style="left: {hoverPct}%;">
						{formatTime(hoverTime)}
					</div>
				{/if}
			</div>
			<span class="time-remaining" title="Remaining time">
				-{formatTime(Math.max(0, duration - currentTime))}
			</span>
		</div>

		<!-- Control Buttons Row -->
		<div class="controls-row">
			<div class="controls-left">
				<button
					type="button"
					class="icon-btn play-btn"
					onclick={togglePlay}
					title={isPaused ? 'Play (Space/k)' : 'Pause (Space/k)'}
					aria-label={isPaused ? 'Play' : 'Pause'}
				>
					{#if isPaused}
						<Play size={22} />
					{:else}
						<Pause size={22} />
					{/if}
				</button>

				<button
					type="button"
					class="icon-btn"
					onclick={() => seekBy(-10)}
					title="Replay 10 seconds (Left Arrow/j)"
					aria-label="Replay 10 seconds"
				>
					<RotateCcw size={19} />
				</button>

				<button
					type="button"
					class="icon-btn"
					onclick={() => seekBy(10)}
					title="Forward 10 seconds (Right Arrow/l)"
					aria-label="Forward 10 seconds"
				>
					<RotateCw size={19} />
				</button>

				<!-- Volume; the slider reveals on hover or focus -->
				<div class="volume-group">
					<button
						type="button"
						class="icon-btn"
						onclick={toggleMute}
						title={isMuted ? 'Unmute (m)' : 'Mute (m)'}
						aria-label={isMuted ? 'Unmute' : 'Mute'}
					>
						{#if isMuted || volume === 0}
							<VolumeX size={20} />
						{:else if volume < 0.5}
							<Volume1 size={20} />
						{:else}
							<Volume2 size={20} />
						{/if}
					</button>
					<div class="volume-slider-wrap">
						<input
							type="range"
							min="0"
							max="1"
							step="0.05"
							value={isMuted ? 0 : volume}
							oninput={(e) => handleVolumeInput(Number.parseFloat(e.currentTarget.value))}
							class="volume-slider"
							aria-label="Volume slider"
						/>
					</div>
				</div>
			</div>

			{#if endsAt}
				<span class="end-time" title="Ends at {endsAt}">End at {endsAt}</span>
			{/if}

			<div class="controls-right">
				<!-- Audio selector -->
				{#if availableAudio.length > 1}
					<button
						type="button"
						class="icon-btn"
						class:active={openMenu === 'audio' || audioTrackIndex !== null}
						onclick={() => toggleMenu('audio')}
						title="Audio track"
						aria-label="Select audio track"
						aria-haspopup="menu"
						aria-expanded={openMenu === 'audio'}
					>
						<Languages size={18} />
					</button>
				{/if}

				<!-- Subtitles selector -->
				{#if availableSubtitles.length > 0 || availableEmbeddedSubtitles.length > 0}
					<button
						type="button"
						class="icon-btn"
						class:active={openMenu === 'subtitle' || subtitleChoice !== ''}
						onclick={() => toggleMenu('subtitle')}
						title="Subtitles"
						aria-label="Select subtitle track"
						aria-haspopup="menu"
						aria-expanded={openMenu === 'subtitle'}
					>
						<Captions size={19} />
					</button>

					<!-- Subtitle Sync Button -->
					<button
						type="button"
						class="icon-btn"
						class:active={showSubSync}
						onclick={() => {
							openMenu = null;
							showSubSync = !showSubSync;
						}}
						title="Subtitle Sync / Calibration"
						aria-label="Subtitle synchronization"
					>
						<SlidersHorizontal size={18} />
					</button>
				{/if}

				<!-- Playback rate -->
				<button
					type="button"
					class="icon-btn"
					class:active={openMenu === 'speed' || playbackRate !== 1}
					onclick={() => toggleMenu('speed')}
					title="Playback speed"
					aria-label="Playback speed"
					aria-haspopup="menu"
					aria-expanded={openMenu === 'speed'}
				>
					<Gauge size={18} />
				</button>

				<!-- Quality Selector -->
				{#if levels.length > 1}
					<label class="select-label">
						<span class="visually-hidden">Quality</span>
						<select
							class="control-select"
							value={qualityLevel}
							onchange={handleQualityChange}
							aria-label="Quality selection"
						>
							<option value={-1}>Auto</option>
							{#each levels as level (level.index)}
								<option value={level.index}>
									{level.height}p ({formatBitrate(level.bitrate)})
								</option>
							{/each}
						</select>
					</label>
				{/if}

				<!-- Stream Info -->
				{#if info}
					<button
						type="button"
						class="icon-btn"
						class:active={showModeBadge}
						onclick={() => (showModeBadge = !showModeBadge)}
						title="Stream Info / Transcode Status"
						aria-label="Stream information"
					>
						<Info size={19} />
					</button>
				{/if}

				<!-- Picture in Picture -->
				<button
					type="button"
					class="icon-btn"
					onclick={togglePip}
					title="Picture in Picture (p)"
					aria-label="Picture in picture"
				>
					<PictureInPicture2 size={19} />
				</button>

				<!-- Fullscreen -->
				<button
					type="button"
					class="icon-btn"
					onclick={toggleFullscreen}
					title={isFullscreen ? 'Exit Fullscreen (f)' : 'Fullscreen (f)'}
					aria-label={isFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
				>
					{#if isFullscreen}
						<Minimize size={20} />
					{:else}
						<Maximize size={20} />
					{/if}
				</button>
			</div>
		</div>
	</div>

	<!-- Subtitle Sync Control Modal -->
	{#if showSubSync}
		<SubtitleSyncControl
			{itemId}
			currentOffset={currentSubOffset}
			onOffsetChange={handleOffsetSyncChange}
			onClose={() => (showSubSync = false)}
		/>
	{/if}

	<!-- Mode Badge Popup -->
	{#if showModeBadge && info}
		<div
			class="mode-badge-popup"
			class:hidden={!isPaused && !isUserActive && !loading && !loadError}
			role="status"
			title={(info.reasons ?? []).join('\n')}
		>
			<span class="mode-line mode-headline">{playbackModeDetail()}</span>
			<span class="mode-line">{videoStreamDetail()}</span>
			<span class="mode-line">{audioStreamDetail()}</span>
			<span class="mode-line">
				{formatBitrate(streamBitrate)}{#if streamThroughput}
					· net {formatBitrate(streamThroughput)}
				{/if}
			</span>
		</div>
	{/if}
</div>

<style>
	.player {
		position: relative;
		width: 100%;
		height: 100vh;
		background: #000;
		overflow: hidden;
		display: flex;
		flex-direction: column;
		user-select: none;
	}

	.player.user-inactive {
		cursor: none;
	}

	.video-container {
		width: 100%;
		height: 100%;
		flex: 1;
		min-height: 0;
		background: #000;
		overflow: hidden;
		position: relative;
		margin: 0;
		display: flex;
		align-items: center;
		justify-content: center;
	}

	video {
		width: 100%;
		height: 100%;
		background-color: #000;
		border-radius: 0;
		outline: none;
	}

	:global(.video-container .JASSUB) {
		position: absolute;
		top: 0;
		left: 0;
		width: 100%;
		height: 100%;
		pointer-events: none;
		z-index: 1;
	}

	.top-overlay {
		position: absolute;
		top: 0;
		left: 0;
		right: 0;
		z-index: 10;
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		padding: 1rem 1.5rem;
		background: linear-gradient(
			to bottom,
			rgba(0, 0, 0, 0.85) 0%,
			rgba(0, 0, 0, 0.4) 60%,
			transparent 100%
		);
		transition:
			opacity 0.15s ease,
			transform 0.15s ease;
	}

	.top-overlay.hidden {
		opacity: 0;
		pointer-events: none;
		transform: translateY(-0.5rem);
	}

	.top-overlay:hover,
	.top-overlay:focus-within {
		opacity: 1 !important;
		pointer-events: auto !important;
		transform: none !important;
	}

	.top-left {
		display: flex;
		align-items: center;
		gap: 1rem;
		min-width: 0;
		flex: 1;
	}

	.back-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2.5rem;
		height: 2.5rem;
		border-radius: 50%;
		background: rgba(22, 27, 34, 0.7);
		border: 1px solid var(--border);
		color: var(--text);
		cursor: pointer;
		text-decoration: none;
		flex-shrink: 0;
		backdrop-filter: blur(4px);
		transition:
			background 0.15s ease,
			border-color 0.15s ease,
			color 0.15s ease;
	}

	.back-btn:hover {
		background: var(--surface-2);
		border-color: var(--accent);
		color: var(--accent);
		text-decoration: none;
	}

	.player-title {
		font-size: 1.15rem;
		font-weight: 600;
		color: #fff;
		margin: 0;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
		text-shadow: 0 1px 4px rgba(0, 0, 0, 0.8);
	}

	.bottom-overlay {
		position: absolute;
		bottom: 0;
		left: 0;
		right: 0;
		z-index: 10;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		padding: 1.5rem 1.5rem 1rem;
		background: linear-gradient(
			to top,
			rgba(0, 0, 0, 0.9) 0%,
			rgba(0, 0, 0, 0.5) 70%,
			transparent 100%
		);
		transition:
			opacity 0.15s ease,
			transform 0.15s ease;
	}

	.bottom-overlay.hidden {
		opacity: 0;
		pointer-events: none;
		transform: translateY(0.5rem);
	}

	.bottom-overlay:hover,
	.bottom-overlay:focus-within {
		opacity: 1 !important;
		pointer-events: auto !important;
		transform: none !important;
	}

	.seek-row {
		display: flex;
		align-items: center;
		gap: 0.65rem;
		width: 100%;
	}

	.seekbar-container {
		position: relative;
		flex: 1;
		min-width: 0;
		height: 1.25rem;
		display: flex;
		align-items: center;
		cursor: pointer;
		outline: none;
	}

	.seekbar-track {
		position: relative;
		width: 100%;
		height: 5px;
		background: rgba(255, 255, 255, 0.25);
		border-radius: 3px;
		transition: height 0.15s ease;
	}

	.seekbar-container:hover .seekbar-track,
	.seekbar-container:focus-visible .seekbar-track {
		height: 8px;
	}

	.buffered-bar {
		position: absolute;
		top: 0;
		bottom: 0;
		background: rgba(255, 255, 255, 0.4);
		border-radius: 3px;
		pointer-events: none;
	}

	.played-bar {
		position: absolute;
		top: 0;
		bottom: 0;
		left: 0;
		background: var(--accent);
		border-radius: 3px;
		pointer-events: none;
	}

	.scrub-thumb {
		position: absolute;
		top: 50%;
		width: 13px;
		height: 13px;
		border-radius: 50%;
		background: #fff;
		transform: translate(-50%, -50%) scale(0);
		box-shadow: 0 0 6px rgba(0, 0, 0, 0.6);
		pointer-events: none;
		transition: transform 0.15s ease;
	}

	.seekbar-container:hover .scrub-thumb,
	.seekbar-container:focus-visible .scrub-thumb {
		transform: translate(-50%, -50%) scale(1);
	}

	.hover-tooltip {
		position: absolute;
		bottom: 1.5rem;
		transform: translateX(-50%);
		background: rgba(14, 17, 22, 0.95);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0.2rem 0.45rem;
		font-size: 0.75rem;
		font-family: ui-monospace, monospace;
		color: #fff;
		pointer-events: none;
		white-space: nowrap;
	}

	.controls-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		width: 100%;
	}

	.controls-left,
	.controls-right {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		flex: 1 1 0;
		flex-wrap: wrap;
	}

	.controls-right {
		justify-content: flex-end;
	}

	.icon-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		background: transparent;
		border: none;
		color: var(--text);
		padding: 0.4rem;
		border-radius: 6px;
		cursor: pointer;
		transition:
			background 0.15s ease,
			color 0.15s ease;
	}

	.icon-btn:hover {
		background: rgba(255, 255, 255, 0.15);
		color: #fff;
	}

	.icon-btn.active {
		color: var(--accent);
	}

	.icon-btn:focus-visible {
		outline: 2px solid var(--accent);
		outline-offset: 1px;
	}

	.play-btn {
		padding: 0.5rem;
	}

	.end-time {
		flex: 0 0 auto;
		font-size: 0.8rem;
		font-family: ui-monospace, monospace;
		color: var(--muted);
		white-space: nowrap;
		font-variant-numeric: tabular-nums;
		text-align: center;
	}

	.volume-group {
		display: flex;
		align-items: center;
		gap: 0.25rem;
	}

	.volume-slider-wrap {
		display: flex;
		align-items: center;
		max-width: 0;
		opacity: 0;
		overflow: hidden;
		transition:
			max-width 0.18s ease,
			opacity 0.18s ease;
	}

	.volume-group:hover .volume-slider-wrap,
	.volume-group:focus-within .volume-slider-wrap {
		max-width: 5rem;
		opacity: 1;
	}

	.volume-slider {
		width: 4.5rem;
		height: 4px;
		accent-color: var(--accent);
		cursor: pointer;
	}

	.time-current,
	.time-remaining {
		font-size: 0.825rem;
		font-family: ui-monospace, monospace;
		white-space: nowrap;
		font-variant-numeric: tabular-nums;
	}

	.time-current {
		color: #fff;
	}

	.time-remaining {
		color: var(--muted);
	}

	.select-label {
		display: flex;
		align-items: center;
	}

	.control-select {
		background: rgba(22, 27, 34, 0.85);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.3rem 0.55rem;
		font-size: 0.8rem;
		color: var(--text);
		backdrop-filter: blur(4px);
		cursor: pointer;
		outline: none;
		max-width: 9rem;
		text-overflow: ellipsis;
	}

	.control-select:focus-visible {
		border-color: var(--accent);
		outline: 2px solid var(--accent);
	}

	.control-select:hover {
		border-color: var(--accent);
	}

	.control-menu {
		position: absolute;
		right: 1.5rem;
		bottom: 100%;
		z-index: 20;
		display: flex;
		flex-direction: column;
		gap: 0.1rem;
		min-width: 11rem;
		max-width: min(20rem, 80vw);
		max-height: 22rem;
		overflow-y: auto;
		margin-bottom: 0.4rem;
		padding: 0.4rem;
		background: rgba(14, 17, 22, 0.96);
		border: 1px solid var(--border);
		border-radius: 10px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.55);
		backdrop-filter: blur(10px);
	}

	.menu-heading {
		padding: 0.3rem 0.6rem 0.2rem;
		font-size: 0.7rem;
		font-weight: 600;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--muted);
	}

	.menu-item {
		background: transparent;
		border: none;
		border-radius: 6px;
		padding: 0.45rem 0.6rem;
		color: var(--text);
		font-size: 0.85rem;
		text-align: left;
		cursor: pointer;
	}

	.menu-item:hover,
	.menu-item:focus-visible {
		background: rgba(255, 255, 255, 0.12);
		color: #fff;
		outline: none;
	}

	.menu-item.selected {
		color: var(--accent);
		font-weight: 600;
	}

	.buffering-spinner {
		position: absolute;
		top: 50%;
		left: 50%;
		width: 3.5rem;
		height: 3.5rem;
		margin-top: -1.75rem;
		margin-left: -1.75rem;
		border: 3px solid rgba(255, 255, 255, 0.2);
		border-top-color: var(--accent);
		border-radius: 50%;
		animation: spin 0.8s linear infinite;
		pointer-events: none;
		z-index: 5;
	}

	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}

	.mode-badge-popup {
		position: absolute;
		top: 4.5rem;
		right: 1.5rem;
		z-index: 15;
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		font-size: 0.825rem;
		font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
		font-weight: 500;
		letter-spacing: 0.02em;
		color: #fff;
		background: rgba(14, 17, 22, 0.9);
		border: 1px solid var(--border);
		border-radius: 10px;
		padding: 0.55rem 0.8rem;
		backdrop-filter: blur(8px);
		box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
		pointer-events: auto;
		transition:
			opacity 0.25s ease,
			transform 0.25s ease;
	}

	.mode-badge-popup.hidden {
		opacity: 0;
		pointer-events: none;
		transform: translateY(-0.5rem);
	}

	.mode-line {
		white-space: nowrap;
	}

	.mode-headline {
		font-weight: 700;
		color: var(--accent);
	}

	.banner {
		position: absolute;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		z-index: 25;
		background: var(--surface);
		border: 1px solid var(--border);
		border-left: 4px solid var(--danger);
		border-radius: var(--radius);
		padding: 1rem 1.5rem;
		color: var(--text);
		max-width: min(90vw, 30rem);
		box-shadow: 0 8px 30px rgba(0, 0, 0, 0.5);
	}

	.status {
		position: absolute;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		z-index: 25;
		color: var(--muted);
		font-size: 1.1rem;
		background: rgba(22, 27, 34, 0.8);
		backdrop-filter: blur(8px);
		padding: 0.75rem 1.5rem;
		border-radius: var(--radius);
		border: 1px solid var(--border);
	}
</style>
