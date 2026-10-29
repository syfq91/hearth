<script lang="ts">
	import Hls from 'hls.js';
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

	let video = $state<HTMLVideoElement | null>(null);
	let info = $state<PlaybackInfoResponse | null>(null);
	let loadError = $state<string | null>(null);
	let loading = $state(true);
	let audioTrackIndex = $state<number | null>(null);
	/** Embedded subtitle stream handed to the transcode plan, if any. */
	let embeddedSubtitleIndex = $state<number | null>(null);

	let hls: Hls | null = null;
	/** Server-side HLS session for `info`; plain so re-planning cannot loop effects. */
	let sessionId: string | null = null;
	let resumeApplied = false;
	let lastReportedMs = 0;
	let destroyed = false;

	/** HLS rendition list for the quality selector (empty = not applicable). */
	let levels = $state<{ index: number; label: string }[]>([]);
	/** Selected rendition index, or -1 for auto. */
	let qualityLevel = $state(-1);

	// Progress checkpoints (docs/API.md §4: resume and next-up depend on
	// them): every 15s of playback, immediately on pause, and on unload.
	const REPORT_INTERVAL_MS = 15_000;

	async function report(state: 'playing' | 'paused' | 'stopped'): Promise<void> {
		const element = video;
		if (!element) return;
		const position_ms = Math.max(0, Math.floor(element.currentTime * 1000));
		const duration_ms = Number.isFinite(element.duration)
			? Math.floor(element.duration * 1000)
			: undefined;
		lastReportedMs = Math.max(lastReportedMs, position_ms);
		try {
			await api.post(`/items/${itemId}/progress`, {
				body: { position_ms, duration_ms, state }
			});
		} catch {
			/* progress is best-effort; never block playback on it */
		}
	}

	function handleTimeUpdate(): void {
		const element = video;
		if (!element) return;
		const positionMs = Math.floor(element.currentTime * 1000);
		if (positionMs - lastReportedMs >= REPORT_INTERVAL_MS) {
			lastReportedMs = positionMs;
			void report('playing');
		}
	}

	/** (Re)start playback with the current track choices. */
	async function startPlayback(): Promise<void> {
		destroyed = false;
		loading = true;
		loadError = null;
		// The component is reused across episodes: tear down the previous
		// source and its server-side session before planning the next one.
		hls?.destroy();
		hls = null;
		const previousSession = sessionId;
		sessionId = null;
		info = null;
		if (previousSession) {
			void api.post(`/hls/${previousSession}/stop`, { auth: false }).catch(() => {});
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
		const element = video;
		if (!element) return;
		resumeApplied = false;
		lastReportedMs = 0;
		levels = [];
		qualityLevel = -1;
		const source = response.mode === 'direct_play' ? response.url : response.hls_url;
		if (!source) {
			loadError = 'The server returned no playable source.';
			loading = false;
			return;
		}
		hls?.destroy();
		hls = null;
		if (response.mode !== 'direct_play' && Hls.isSupported()) {
			const instance = new Hls({ enableWorker: true });
			hls = instance;
			instance.loadSource(source);
			instance.attachMedia(element);
			instance.on(Hls.Events.MANIFEST_PARSED, () => {
				// Quality selector: one entry per rendition, auto when none picked.
				const parsed = instance.levels.map((level, index) => ({
					index,
					label: level.height
						? `${level.height}p (${formatBitrate(level.bitrate)})`
						: `${formatBitrate(level.bitrate)}`
				}));
				levels = parsed.length > 1 ? parsed : [];
				qualityLevel = -1;
				loading = false;
				void element.play().catch(() => {
					/* autoplay blocked: the controls are right there */
				});
			});
			instance.on(Hls.Events.ERROR, (_event, data) => {
				if (data.fatal && hls === instance) {
					loadError = `Playback failed: ${data.details}`;
					instance.destroy();
					hls = null;
				}
			});
		} else {
			// Direct play, or native HLS (Safari and some TV browsers).
			element.src = source;
			loading = false;
		}
		applySubtitleChoice();
	}

	function formatBitrate(bitsPerSecond: number | undefined): string {
		if (!bitsPerSecond) return 'unknown bitrate';
		const mbps = bitsPerSecond / 1_000_000;
		return mbps >= 1 ? `${mbps.toFixed(1)} Mbps` : `${Math.round(bitsPerSecond / 1000)} kbps`;
	}

	/** Apply the quality select: -1 returns to ABR. */
	function applyQuality(): void {
		if (hls) hls.currentLevel = qualityLevel;
	}

	function applyResume(): void {
		const element = video;
		if (resumeApplied || !element || startAtMs <= 0) return;
		resumeApplied = true;
		const durationMs = element.duration * 1000;
		// Near the end the user wants a rewatch prompt, not a jump to credits.
		if (Number.isFinite(durationMs) && durationMs - startAtMs < 30_000) return;
		element.currentTime = startAtMs / 1000;
	}

	function togglePlay(): void {
		const element = video;
		if (!element) return;
		if (element.paused) void element.play().catch(() => {});
		else element.pause();
	}

	function seekBy(deltaSeconds: number): void {
		const element = video;
		if (!element) return;
		element.currentTime = Math.max(0, element.currentTime + deltaSeconds);
	}

	function handleKeydown(event: KeyboardEvent): void {
		const target = event.target as HTMLElement | null;
		if (target && (target.tagName === 'INPUT' || target.tagName === 'SELECT')) return;
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
			case 'f':
				event.preventDefault();
				void video?.parentElement?.requestFullscreen?.().catch(() => {});
				break;
			default:
				break;
		}
	}

	/**
	 * Subtitle choice in the select: `ext:<id>` for a sidecar file the browser
	 * renders from a `<track>`, `emb:<index>` for an embedded stream the
	 * server must mux into the plan, or empty for off.
	 */
	let subtitleChoice = $state('');

	/** Applied to the media element after each (re)attach. */
	function applySubtitleChoice(): void {
		const element = video;
		if (!element) return;
		const externalId = subtitleChoice.startsWith('ext:')
			? subtitleChoice.slice('ext:'.length)
			: null;
		const tracks = element.textTracks;
		for (let i = 0; i < tracks.length; i += 1) {
			tracks[i].mode = externalId !== null && tracks[i].id === externalId ? 'showing' : 'disabled';
		}
	}

	$effect(() => {
		if (video) void startPlayback();
	});

	// Unload reporting: keep the resume point when the tab is hidden or closed.
	$effect(() => {
		const onHide = () => void report('paused');
		window.addEventListener('pagehide', onHide);
		document.addEventListener('visibilitychange', onHide);
		return () => {
			window.removeEventListener('pagehide', onHide);
			document.removeEventListener('visibilitychange', onHide);
		};
	});

	$effect(() => {
		// Unmount-only cleanup: the effect body reads no reactive state, so it
		// runs once and the teardown fires when the component is destroyed.
		return () => {
			destroyed = true;
			void report('stopped');
			hls?.destroy();
			hls = null;
			const session = sessionId;
			if (session) {
				void api.post(`/hls/${session}/stop`, { auth: false }).catch(() => {});
			}
		};
	});

	const availableAudio = $derived(info?.audio_streams ?? []);
	const availableSubtitles = $derived(info?.subtitles ?? []);
	const availableEmbeddedSubtitles = $derived(info?.embedded_subtitles ?? []);
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="player">
	{#if loadError}
		<p class="banner" role="alert">{loadError}</p>
	{:else if loading}
		<p class="status" role="status">Preparing playback…</p>
	{/if}

	<!-- svelte-ignore a11y_media_has_caption -->
	<video
		bind:this={video}
		controls
		playsinline
		preload="metadata"
		ontimeupdate={handleTimeUpdate}
		onloadedmetadata={applyResume}
		onpause={() => void report('paused')}
		onended={() => {
			void report('stopped');
			onEnded?.();
		}}
	>
		{#each availableSubtitles as track (track.id)}
			<track
				id={track.id}
				kind="subtitles"
				src={track.url}
				srclang={track.language ?? ''}
				label={track.name}
			/>
		{/each}
	</video>

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

	video {
		width: 100%;
		max-height: 72vh;
		background: #000;
		border-radius: var(--radius);
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
