<script lang="ts">
	import videojs from 'video.js';
	import type Player from 'video.js/dist/types/player';
	import 'video.js/dist/video-js.css';
	import JASSUB from 'jassub';
	import { ArrowLeft, Info } from '@lucide/svelte';
	import { api } from '../api/client';
	import { addAudioMenuButton, type AudioMenuHandle } from '../video-audio-menu';
	import type { PlaybackInfoResponse } from '../api/types';

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
	let player: Player | null = null;
	let jassubInstance: JASSUB | null = null;
	let hasJassub = $state(false);

	let info = $state<PlaybackInfoResponse | null>(null);
	let loadError = $state<string | null>(null);
	let loading = $state(true);
	let audioTrackIndex = $state<number | null>(null);
	/** Embedded subtitle stream handed to the transcode plan, if any. */
	let embeddedSubtitleIndex = $state<number | null>(null);

	/** Server-side HLS session for `info`; plain so re-planning cannot loop effects. */
	let sessionId: string | null = null;
	let resumeApplied = false;
	let lastReportedMs = 0;
	let destroyed = false;
	let focusTaken = false;
	let syncingTracks = false;

	let lastItemId = '';
	let lastAudioTrack: number | null = null;
	let lastSubChoice = '';
	let expectedDuration = 0;

	/** HLS rendition list for the quality selector (empty = not applicable). */
	let levels = $state<{ index: number; label: string }[]>([]);
	/** Selected rendition index, or -1 for auto. */
	let qualityLevel = $state(-1);

	let isUserActive = $state(true);
	let isPaused = $state(true);
	/** Whether the direct/transcode badge is shown (toggled by the info button). */
	let showModeBadge = $state(false);
	let hideTimeout: ReturnType<typeof setTimeout> | null = null;

	function triggerActivity(): void {
		isUserActive = true;
		if (hideTimeout) clearTimeout(hideTimeout);
		if (!isPaused) {
			hideTimeout = setTimeout(() => {
				isUserActive = false;
			}, 3000);
		}
	}

	/**
	 * Subtitle choice in the select: `ext:<id>` for a sidecar file the browser
	 * renders from a `<track>`, `emb:<index>` for an embedded stream the
	 * server must mux into the plan, or empty for off.
	 */
	let subtitleChoice = $state('');

	// Progress checkpoints (docs/API.md §4: resume and next-up depend on
	// them): every 15s of playback, immediately on pause, and on unload.
	const REPORT_INTERVAL_MS = 15_000;

	async function report(state: 'playing' | 'paused' | 'stopped'): Promise<void> {
		if (!player || player.isDisposed()) return;
		const cur = player.currentTime() ?? 0;
		const dur = player.duration();
		const position_ms = Math.max(0, Math.floor(cur * 1000));
		const rawDuration =
			typeof dur === 'number' && Number.isFinite(dur) && dur > 0
				? dur
				: expectedDuration > 0
					? expectedDuration
					: undefined;
		const duration_ms = rawDuration ? Math.floor(rawDuration * 1000) : undefined;
		lastReportedMs = Math.max(lastReportedMs, position_ms);
		try {
			await api.post(`/items/${itemId}/progress`, {
				body: { position_ms, duration_ms, state }
			});
		} catch {
			/* progress is best-effort; never block playback on it */
		}
	}

	function syncDuration(): void {
		if (!player || player.isDisposed() || expectedDuration <= 0) return;
		const dur = player.duration();
		if (!dur || !Number.isFinite(dur) || (info?.mode !== 'direct_play' && dur < expectedDuration)) {
			player.duration(expectedDuration);
		}
	}

	function handleTimeUpdate(): void {
		if (!player || player.isDisposed()) return;
		const cur = player.currentTime() ?? 0;
		const positionMs = Math.floor(cur * 1000);
		if (positionMs - lastReportedMs >= REPORT_INTERVAL_MS) {
			lastReportedMs = positionMs;
			void report('playing');
		}
	}

	function formatBitrate(bitsPerSecond: number | undefined): string {
		if (!bitsPerSecond) return 'unknown bitrate';
		const mbps = bitsPerSecond / 1_000_000;
		return mbps >= 1 ? `${mbps.toFixed(1)} Mbps` : `${Math.round(bitsPerSecond / 1000)} kbps`;
	}

	function updateQualityLevels(): void {
		if (!player || player.isDisposed()) return;
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const ql = (player as any).qualityLevels?.();
		if (!ql || ql.length <= 1) {
			levels = [];
			qualityLevel = -1;
			return;
		}
		const parsed: { index: number; label: string }[] = [];
		for (let i = 0; i < ql.length; i += 1) {
			const lvl = ql[i];
			parsed.push({
				index: i,
				label: lvl.height
					? `${lvl.height}p (${formatBitrate(lvl.bitrate)})`
					: `${formatBitrate(lvl.bitrate)}`
			});
		}
		levels = parsed;
	}

	/** Apply the quality select: -1 returns to ABR. */
	function applyQuality(): void {
		if (!player || player.isDisposed()) return;
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const ql = (player as any).qualityLevels?.();
		if (!ql) return;
		for (let i = 0; i < ql.length; i += 1) {
			ql[i].enabled = qualityLevel === -1 || qualityLevel === i;
		}
	}

	function applyResume(): void {
		if (resumeApplied || !player || player.isDisposed() || startAtMs <= 0) return;
		resumeApplied = true;
		const dur = player.duration() ?? expectedDuration;
		const durationMs = dur * 1000;
		// Near the end the user wants a rewatch prompt, not a jump to credits.
		if (Number.isFinite(durationMs) && durationMs > 30_000 && durationMs - startAtMs < 30_000) return;
		player.currentTime(startAtMs / 1000);
	}

	function togglePlay(): void {
		if (!player || player.isDisposed()) return;
		if (player.paused()) {
			void player.play()?.catch(() => {});
		} else {
			player.pause();
		}
	}

	function seekBy(deltaSeconds: number): void {
		if (!player || player.isDisposed()) return;
		const cur = player.currentTime() ?? 0;
		player.currentTime(Math.max(0, cur + deltaSeconds));
	}

	function handleKeydown(event: KeyboardEvent): void {
		// Keys starting inside the control bar buttons or menus are left to Video.js
		if (
			(event.target as HTMLElement)?.closest('.vjs-control-bar') ||
			(event.target as HTMLElement)?.closest('.vjs-menu')
		) {
			return;
		}
		switch (event.key) {
			case ' ':
			case 'k':
				event.preventDefault();
				event.stopPropagation();
				togglePlay();
				break;
			case 'ArrowRight':
			case 'l':
				event.preventDefault();
				event.stopPropagation();
				seekBy(10);
				break;
			case 'ArrowLeft':
			case 'j':
				event.preventDefault();
				event.stopPropagation();
				seekBy(-10);
				break;
			case 'f':
				event.preventDefault();
				event.stopPropagation();
				if (player?.isFullscreen()) {
					void player.exitFullscreen();
				} else {
					void player?.requestFullscreen();
				}
				break;
			default:
				break;
		}
	}

	function toAssTimestamp(ts: string): string {
		const parts = ts.trim().replace(',', '.').split(':');
		let h = 0;
		let m = 0;
		let s = 0;
		let ms = 0;
		if (parts.length === 3) {
			h = Number.parseInt(parts[0], 10) || 0;
			m = Number.parseInt(parts[1], 10) || 0;
			const secParts = parts[2].split('.');
			s = Number.parseInt(secParts[0], 10) || 0;
			ms = Number.parseInt(secParts[1] ?? '0', 10) || 0;
		} else if (parts.length === 2) {
			m = Number.parseInt(parts[0], 10) || 0;
			const secParts = parts[1].split('.');
			s = Number.parseInt(secParts[0], 10) || 0;
			ms = Number.parseInt(secParts[1] ?? '0', 10) || 0;
		}
		const cs = Math.floor(ms / 10)
			.toString()
			.padStart(2, '0')
			.slice(0, 2);
		return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${cs}`;
	}

	function toAss(content: string): string {
		if (content.includes('[Script Info]') || content.includes('[Events]')) {
			return content;
		}
		const header = [
			'[Script Info]',
			'ScriptType: v4.00+',
			'WrapStyle: 0',
			'ScaledBorderAndShadow: yes',
			'YCbCr Matrix: None',
			'',
			'[V4+ Styles]',
			'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
			'Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1',
			'',
			'[Events]',
			'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text'
		].join('\n');

		const lines = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
		const dialogues: string[] = [];
		const timeRegex =
			/((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{2,3})\s*-->\s*((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{2,3})/;

		let i = 0;
		while (i < lines.length) {
			const line = lines[i].trim();
			const match = timeRegex.exec(line);
			if (match) {
				const start = toAssTimestamp(match[1]);
				const end = toAssTimestamp(match[2]);
				i += 1;
				const textLines: string[] = [];
				while (i < lines.length && lines[i].trim() !== '') {
					textLines.push(lines[i].trim());
					i += 1;
				}
				if (textLines.length > 0) {
					const text = textLines.join('\\N');
					dialogues.push(`Dialogue: 0,${start},${end},Default,,0,0,0,,${text}`);
				}
			} else {
				i += 1;
			}
		}
		return `${header}\n${dialogues.join('\n')}\n`;
	}

	async function destroyJassub(): Promise<void> {
		hasJassub = false;
		if (jassubInstance) {
			const instance = jassubInstance;
			jassubInstance = null;
			try {
				await instance.destroy();
			} catch {
				/* ignore */
			}
		}
	}

	async function applySubtitleChoice(): Promise<void> {
		if (!player || player.isDisposed()) return;
		const externalId = subtitleChoice.startsWith('ext:')
			? subtitleChoice.slice('ext:'.length)
			: null;

		await destroyJassub();

		syncingTracks = true;
		try {
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const tracks = player.textTracks() as any;
			for (let i = 0; i < tracks.length; i += 1) {
				tracks[i].mode = tracks[i].id === subtitleChoice ? 'showing' : 'disabled';
			}

			if (!externalId || !videoEl) return;

			const selectedTrack = (info?.subtitles ?? []).find((t) => t.id === externalId);
			if (!selectedTrack) return;

			const delimiter = selectedTrack.url.includes('?') ? '&' : '?';
			const fetchUrl =
				selectedTrack.format === 'ass' || selectedTrack.format === 'ssa'
					? `${selectedTrack.url}${delimiter}raw=true`
					: selectedTrack.url;

			try {
				const res = await fetch(fetchUrl);
				if (!res.ok) throw new Error(`HTTP ${res.status}`);
				const text = await res.text();
				const subContent = toAss(text);

				if (subtitleChoice === `ext:${externalId}` && videoEl) {
					jassubInstance = new JASSUB({
						video: videoEl,
						subContent
					});
					hasJassub = true;
				}
			} catch (err) {
				console.warn('JASSUB initialization failed, falling back to native text track:', err);
				hasJassub = false;
			}
		} finally {
			syncingTracks = false;
		}
	}

	function getSourceType(mode: string, url: string): string {
		if (mode !== 'direct_play' || url.includes('.m3u8')) {
			return 'application/x-mpegURL';
		}
		if (url.includes('.webm')) return 'video/webm';
		if (url.includes('.ogg')) return 'video/ogg';
		return 'video/mp4';
	}

	/** (Re)start playback with the current track choices. */
	async function startPlayback(): Promise<void> {
		destroyed = false;
		loading = true;
		loadError = null;
		expectedDuration = 0;

		const previousSession = sessionId;
		sessionId = null;
		info = null;
		if (previousSession) {
			void api.post(`/hls/${previousSession}/stop`).catch(() => {});
		}

		embeddedSubtitleIndex = subtitleChoice.startsWith('emb:')
			? Number.parseInt(subtitleChoice.slice('emb:'.length), 10)
			: null;

		let response: PlaybackInfoResponse;
		try {
			response = await api.post<PlaybackInfoResponse>(`/items/${itemId}/playback-info`, {
				body: {
					audio_track_index: audioTrackIndex,
					subtitle_track_index: embeddedSubtitleIndex,
					start_time_offset_seconds: startAtMs > 0 ? startAtMs / 1000 : 0
				}
			});
		} catch (e) {
			loadError = e instanceof Error ? e.message : String(e);
			loading = false;
			return;
		}

		if (destroyed) return;
		info = response;
		sessionId = response.session_id ?? null;
		await attachSource(response);
	}

	async function attachSource(response: PlaybackInfoResponse): Promise<void> {
		if (!player || player.isDisposed()) return;
		resumeApplied = false;
		lastReportedMs = 0;
		levels = [];
		qualityLevel = -1;

		await destroyJassub();

		const source = response.mode === 'direct_play' ? response.url : response.hls_url;
		if (!source) {
			loadError = 'The server returned no playable source.';
			loading = false;
			return;
		}

		syncingTracks = true;
		try {
			// Clear previous remote text tracks
			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const remoteTracks = player.remoteTextTracks() as any;
			while (remoteTracks && remoteTracks.length > 0) {
				player.removeRemoteTextTrack(remoteTracks[0]);
			}

			// Add external subtitles
			for (const track of response.subtitles ?? []) {
				player.addRemoteTextTrack(
					{
						id: `ext:${track.id}`,
						kind: 'subtitles',
						src: track.url,
						srclang: track.language ?? '',
						label: track.name || track.language || 'Subtitles'
					},
					false
				);
			}

			// Add embedded subtitles
			for (const stream of response.embedded_subtitles ?? []) {
				player.addRemoteTextTrack(
					{
						id: `emb:${stream.index}`,
						kind: 'subtitles',
						srclang: stream.language ?? '',
						label: `${stream.title ?? stream.language ?? 'Subtitle'} (embedded)`
					},
					false
				);
			}

			// eslint-disable-next-line @typescript-eslint/no-explicit-any
			const tracks = player.textTracks() as any;
			for (let i = 0; i < tracks.length; i += 1) {
				tracks[i].mode = tracks[i].id === subtitleChoice ? 'showing' : 'disabled';
			}
		} finally {
			syncingTracks = false;
		}

		player.src({
			src: source,
			type: getSourceType(response.mode, source)
		});

		expectedDuration = response.file?.duration_seconds ?? 0;
		syncDuration();

		loading = false;
		void player.play()?.catch(() => {
			/* autoplay blocked: controls are right there */
		});

		void applySubtitleChoice();
	}

	let audioMenu: AudioMenuHandle | null = null;

	function initPlayer(): void {
		if (!containerEl || player) return;

		const video = document.createElement('video');
		video.className = 'video-js vjs-default-skin vjs-big-play-centered';
		video.tabIndex = 0;
		video.setAttribute('playsinline', '');
		video.setAttribute('preload', 'metadata');
		containerEl.appendChild(video);
		videoEl = video;

		video.addEventListener('keydown', handleKeydown);

		const instance = videojs(video, {
			controls: true,
			fill: true,
			responsive: true,
			playbackRates: [0.5, 0.75, 1, 1.25, 1.5, 2],
			controlBar: {
				skipButtons: {
					forward: 10,
					backward: 10
				},
				currentTimeDisplay: true,
				timeDivider: true,
				durationDisplay: true,
				remainingTimeDisplay: false,
				subsCapsButton: true
			},
			html5: {
				vhs: {
					overrideNative: !videojs.browser.IS_ANY_SAFARI
				}
			}
		});

		player = instance;

		audioMenu = addAudioMenuButton(instance, (index) => {
			audioTrackIndex = index;
		});

		instance.on('timeupdate', handleTimeUpdate);
		instance.on('timeupdate', syncDuration);
		instance.on('durationchange', syncDuration);
		instance.on('loadedmetadata', syncDuration);
		instance.on('canplay', syncDuration);
		instance.on('loadedmetadata', applyResume);
		instance.on('canplay', applyResume);
		instance.on('play', () => {
			isPaused = false;
			triggerActivity();
		});
		instance.on('pause', () => {
			isPaused = true;
			isUserActive = true;
			if (hideTimeout) clearTimeout(hideTimeout);
			void report('paused');
		});
		instance.on('useractive', () => triggerActivity());
		instance.on('userinactive', () => {
			if (!isPaused) isUserActive = false;
		});
		instance.on('ended', () => {
			isPaused = true;
			isUserActive = true;
			if (hideTimeout) clearTimeout(hideTimeout);
			void report('stopped');
			onEnded?.();
		});
		instance.on('error', () => {
			const err = instance.error();
			if (err) {
				loadError = `Playback failed: ${err.message || 'unknown error'}`;
			}
		});

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const ql = (instance as any).qualityLevels?.();
		if (ql) {
			ql.on('addqualitylevel', updateQualityLevels);
			ql.on('removequalitylevel', updateQualityLevels);
		}

		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const textTracks = instance.textTracks() as any;
		textTracks?.on('change', () => {
			if (syncingTracks) return;
			let activeId: string | null = null;
			for (let i = 0; i < textTracks.length; i += 1) {
				if (textTracks[i].mode === 'showing') {
					activeId = textTracks[i].id;
					break;
				}
			}
			const targetChoice = activeId ?? '';
			if (subtitleChoice !== targetChoice) {
				subtitleChoice = targetChoice;
			}
		});

		if (!focusTaken) {
			focusTaken = true;
			video.focus({ preventScroll: true });
		}

		lastItemId = itemId;
		lastAudioTrack = audioTrackIndex;
		lastSubChoice = subtitleChoice;
		void startPlayback();
	}

	$effect(() => {
		if (containerEl && !player) {
			initPlayer();
		}
	});

	$effect(() => {
		const currentItem = itemId;
		const currentAudio = audioTrackIndex;
		const currentSub = subtitleChoice;

		if (!player) return;

		if (currentItem !== lastItemId) {
			lastItemId = currentItem;
			lastAudioTrack = null;
			audioTrackIndex = null;
			lastSubChoice = '';
			subtitleChoice = '';
			void startPlayback();
		} else if (currentAudio !== lastAudioTrack) {
			lastAudioTrack = currentAudio;
			void startPlayback();
		} else if (currentSub !== lastSubChoice) {
			const wasEmbedded = lastSubChoice.startsWith('emb:');
			const isEmbedded = currentSub.startsWith('emb:');
			lastSubChoice = currentSub;
			if (isEmbedded || wasEmbedded) {
				void startPlayback();
			} else {
				void applySubtitleChoice();
			}
		}
	});

	// Take keyboard focus on open so the media keys work immediately
	$effect(() => {
		const el = videoEl;
		if (!el || focusTaken) return;
		focusTaken = true;
		el.focus({ preventScroll: true });
	});

	// Cancel native space on video so our custom handler is sole owner
	$effect(() => {
		const el = videoEl;
		if (!el) return;
		const cancelNativeSpace = (event: KeyboardEvent) => {
			if (event.key === ' ' && event.composedPath()[0] === el) {
				event.preventDefault();
			}
		};
		window.addEventListener('keydown', cancelNativeSpace, true);
		return () => window.removeEventListener('keydown', cancelNativeSpace, true);
	});

	// Unload reporting: keep the resume point when the tab is hidden or closed
	$effect(() => {
		const onHide = () => void report('paused');
		window.addEventListener('pagehide', onHide);
		document.addEventListener('visibilitychange', onHide);
		return () => {
			window.removeEventListener('pagehide', onHide);
			document.removeEventListener('visibilitychange', onHide);
		};
	});

	// Unmount cleanup
	$effect(() => {
		return () => {
			if (hideTimeout) clearTimeout(hideTimeout);
			destroyed = true;
			expectedDuration = 0;
			void report('stopped');
			const session = sessionId;
			if (session) {
				void api.post(`/hls/${session}/stop`).catch(() => {});
			}
			void destroyJassub();
			if (player && !player.isDisposed()) {
				player.dispose();
				player = null;
				videoEl = null;
				audioMenu = null;
			}
		};
	});

	const availableAudio = $derived(info?.audio_streams ?? []);

	// Keep the control-bar audio menu in sync with the current playback plan.
	$effect(() => {
		const choices = availableAudio;
		const selected = audioTrackIndex;
		if (!audioMenu) return;
		audioMenu.setChoices(
			choices.length > 1
				? [
						{ index: null, label: 'Default audio' },
						...choices.map((track) => ({
							index: track.index,
							label: `${track.title ?? track.language ?? 'Audio'} (${track.codec_name ?? '?'})`
						}))
					]
				: [],
			selected
		);
	});
</script>

<svelte:window
	onpointermove={triggerActivity}
	onpointerdown={triggerActivity}
	onkeydown={triggerActivity}
/>

<div class="player">
	{#if loadError}
		<p class="banner" role="alert">{loadError}</p>
	{:else if loading}
		<p class="status" role="status">Preparing playback…</p>
	{/if}

	<div class="video-container" class:has-jassub={hasJassub} bind:this={containerEl}></div>

	<div class="top-overlay" class:hidden={!isPaused && !isUserActive && !loading && !loadError}>
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

		<div class="modes">
			{#if info}
				<button
					type="button"
					class="info-btn"
					class:active={showModeBadge}
					aria-pressed={showModeBadge}
					aria-label="Playback details"
					title="Playback details"
					onclick={() => (showModeBadge = !showModeBadge)}
				>
					<Info size={18} />
				</button>
				{#if showModeBadge}
					<span class="mode" role="status" title={(info.reasons ?? []).join('; ')}>
						{info.mode.replace('_', ' ')}
					</span>
				{/if}
			{/if}
			{#if levels.length > 1}
				<label>
					<span class="visually-hidden">Quality</span>
					<select
						value={qualityLevel}
						onchange={(event) => {
							qualityLevel = Number.parseInt(event.currentTarget.value, 10);
							applyQuality();
						}}
					>
						<option value={-1}>Auto quality</option>
						{#each levels as level (level.index)}
							<option value={level.index}>{level.label}</option>
						{/each}
					</select>
				</label>
			{/if}
		</div>
	</div>
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
	}

	:global(.video-container .video-js) {
		width: 100% !important;
		height: 100% !important;
		background-color: #000;
		border-radius: 0;
	}

	:global(.video-container .video-js .vjs-tech) {
		object-fit: contain;
		width: 100%;
		height: 100%;
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

	:global(.video-container .vjs-big-play-button) {
		background-color: rgba(22, 27, 34, 0.85);
		border-color: var(--border);
		border-radius: var(--radius);
		font-size: 2.5em;
		height: 1.8em;
		line-height: 1.8em;
		margin-top: -0.9em;
		margin-left: -1.5em;
		width: 3em;
		transition:
			background-color 0.2s,
			border-color 0.2s,
			transform 0.2s;
	}

	:global(.video-container .video-js:hover .vjs-big-play-button),
	:global(.video-container .video-js .vjs-big-play-button:focus) {
		background-color: var(--accent);
		border-color: var(--accent);
		color: var(--accent-contrast);
	}

	:global(.video-container .vjs-control-bar) {
		/* Video.js defaults to 3em; double it so the bar is easier to tap. */
		height: 6em;
		align-items: center;
		background-color: rgba(14, 17, 22, 0.85);
		backdrop-filter: blur(8px);
		z-index: 2;
	}

	:global(.video-container .video-js:not(.vjs-live) .vjs-current-time),
	:global(.video-container .video-js:not(.vjs-live) .vjs-time-divider),
	:global(.video-container .video-js:not(.vjs-live) .vjs-duration) {
		display: flex;
		align-items: center;
	}

	:global(.video-container .video-js .vjs-time-divider) {
		min-width: auto;
		padding-left: 0.25rem;
		padding-right: 0.25rem;
	}

	:global(.video-container .video-js .vjs-current-time) {
		padding-right: 0.25rem;
	}

	:global(.video-container .video-js .vjs-duration) {
		padding-left: 0.25rem;
	}

	:global(.video-container .video-js .vjs-remaining-time) {
		display: none;
	}

	:global(.video-container .vjs-play-progress) {
		background-color: var(--accent);
	}

	:global(.video-container .vjs-volume-level) {
		background-color: var(--accent);
	}

	:global(.video-container .vjs-slider) {
		background-color: rgba(255, 255, 255, 0.2);
	}

	:global(.video-container .vjs-load-progress) {
		background: rgba(255, 255, 255, 0.3);
	}

	:global(.video-container.has-jassub .vjs-text-track-display) {
		display: none !important;
	}

	:global(.video-container .vjs-texttrack-settings) {
		display: none !important;
	}

	:global(.video-container .vjs-menu-button-popup .vjs-menu) {
		/* Clear the doubled (6em) control bar. */
		margin-bottom: 6em;
	}

	:global(.video-container .vjs-menu-content) {
		background-color: rgba(14, 17, 22, 0.95) !important;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
		padding: 0.25rem 0;
	}

	:global(.video-container .vjs-menu-item) {
		text-transform: none !important;
		font-family: inherit;
		font-size: 0.875rem;
		padding: 0.4rem 1rem !important;
		color: var(--text) !important;
		display: flex;
		align-items: center;
		transition:
			background-color 0.15s ease,
			color 0.15s ease;
	}

	:global(.video-container .vjs-menu-item:hover),
	:global(.video-container .vjs-menu-item:focus) {
		background-color: var(--surface-2) !important;
		color: #fff !important;
	}

	:global(.video-container .vjs-menu-item.vjs-selected) {
		background-color: rgba(255, 138, 61, 0.15) !important;
		color: var(--accent) !important;
		font-weight: 600;
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
			opacity 0.25s ease,
			transform 0.25s ease;
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

	.modes {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		flex-shrink: 0;
	}

	.info-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2rem;
		height: 2rem;
		border-radius: 50%;
		background: rgba(22, 27, 34, 0.75);
		border: 1px solid var(--border);
		color: var(--text);
		cursor: pointer;
		backdrop-filter: blur(4px);
		transition:
			background 0.15s ease,
			border-color 0.15s ease,
			color 0.15s ease;
	}

	.info-btn:hover,
	.info-btn:focus-visible {
		background: var(--surface-2);
		border-color: var(--accent);
		color: var(--accent);
	}

	.info-btn.active {
		background: rgba(255, 138, 61, 0.15);
		border-color: var(--accent);
		color: var(--accent);
	}

	.mode {
		font-size: 0.8rem;
		color: var(--muted);
		background: rgba(22, 27, 34, 0.75);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0.2rem 0.65rem;
		backdrop-filter: blur(4px);
	}

	select {
		background: rgba(22, 27, 34, 0.75);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.35rem 0.6rem;
		color: var(--text);
		backdrop-filter: blur(4px);
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
