<script lang="ts">
	import videojs from 'video.js';
	import type Player from 'video.js/dist/types/player';
	import 'video.js/dist/video-js.css';
	import JASSUB from 'jassub';
	import { api } from '../api/client';
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
		if (Number.isFinite(durationMs) && durationMs - startAtMs < 30_000) return;
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
		if ((event.target as HTMLElement)?.closest('.vjs-control-bar')) {
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
				tracks[i].mode = 'disabled';
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
				}
			} catch (err) {
				console.warn('JASSUB initialization failed, falling back to native text track:', err);
				for (let i = 0; i < tracks.length; i += 1) {
					const track = tracks[i];
					if (track.id === externalId) {
						track.mode = 'showing';
					}
				}
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
					subtitle_track_index: embeddedSubtitleIndex
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
					id: track.id,
					kind: 'subtitles',
					src: track.url,
					srclang: track.language ?? '',
					label: track.name
				},
				false
			);
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
				remainingTimeDisplay: false
			},
			html5: {
				vhs: {
					overrideNative: !videojs.browser.IS_ANY_SAFARI
				}
			}
		});

		player = instance;

		instance.on('timeupdate', handleTimeUpdate);
		instance.on('timeupdate', syncDuration);
		instance.on('durationchange', syncDuration);
		instance.on('loadedmetadata', syncDuration);
		instance.on('canplay', syncDuration);
		instance.on('loadedmetadata', applyResume);
		instance.on('canplay', applyResume);
		instance.on('pause', () => void report('paused'));
		instance.on('ended', () => {
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
			const targetChoice = activeId ? `ext:${activeId}` : '';
			if (subtitleChoice !== targetChoice && !subtitleChoice.startsWith('emb:')) {
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
			lastSubChoice = currentSub;
			if (currentSub.startsWith('emb:')) {
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
			}
		};
	});

	const availableAudio = $derived(info?.audio_streams ?? []);
	const availableSubtitles = $derived(info?.subtitles ?? []);
	const availableEmbeddedSubtitles = $derived(info?.embedded_subtitles ?? []);
</script>

<div class="player">
	{#if loadError}
		<p class="banner" role="alert">{loadError}</p>
	{:else if loading}
		<p class="status" role="status">Preparing playback…</p>
	{/if}

	<div class="video-container" bind:this={containerEl}></div>

	<div class="bar">
		<h1 class="title">{title}</h1>
		<div class="modes">
			{#if info}
				<span class="mode" title={(info.reasons ?? []).join('; ')}>
					{info.mode.replace('_', ' ')}
				</span>
			{/if}
			{#if availableAudio.length > 1}
				<label>
					<span class="visually-hidden">Audio track</span>
					<select bind:value={audioTrackIndex}>
						<option value={null}>Default audio</option>
						{#each availableAudio as track (track.index)}
							<option value={track.index}>
								{track.title ?? track.language ?? 'Audio'} ({track.codec_name ?? '?'})
							</option>
						{/each}
					</select>
				</label>
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
			{#if availableSubtitles.length > 0 || availableEmbeddedSubtitles.length > 0}
				<label>
					<span class="visually-hidden">Subtitles</span>
					<select bind:value={subtitleChoice}>
						<option value="">Subtitles off</option>
						{#each availableSubtitles as track (track.id)}
							<option value={`ext:${track.id}`}>
								{track.name || track.language || 'Subtitles'}
							</option>
						{/each}
						{#each availableEmbeddedSubtitles as stream (stream.index)}
							<option value={`emb:${stream.index}`}>
								{stream.title ?? stream.language ?? 'Subtitle'} (embedded)
							</option>
						{/each}
					</select>
				</label>
			{/if}
		</div>
	</div>

	<p class="hint">Keyboard: space play/pause · ←/→ seek 10s · f fullscreen</p>
</div>

<style>
	.player {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.video-container {
		width: 100%;
		aspect-ratio: 16 / 9;
		max-height: 72vh;
		background: #000;
		border-radius: var(--radius);
		overflow: hidden;
		position: relative;
		margin: 0 auto;
	}

	:global(.video-container .video-js) {
		width: 100%;
		height: 100%;
		border-radius: var(--radius);
		background-color: #000;
	}

	:global(.video-container .video-js .vjs-tech) {
		object-fit: contain;
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

	.bar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 1rem;
	}

	.title {
		font-size: 1.2rem;
		margin: 0;
		flex: 1;
	}

	.modes {
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}

	.mode {
		font-size: 0.8rem;
		color: var(--muted);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0.15rem 0.6rem;
	}

	select {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.3rem 0.5rem;
	}

	.banner {
		background: var(--surface);
		border: 1px solid var(--border);
		border-left: 3px solid var(--danger);
		border-radius: var(--radius);
		padding: 0.6rem 1rem;
		color: var(--muted);
	}

	.status {
		color: var(--muted);
	}

	.hint {
		color: var(--muted);
		font-size: 0.85rem;
		margin: 0;
	}
</style>
