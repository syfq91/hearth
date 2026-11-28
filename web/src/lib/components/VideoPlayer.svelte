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
		Gauge,
		MonitorPlay,
		Settings2
	} from '@lucide/svelte';
	import { api } from '../api/client';
	import { PlaybackController, type QualityLevel } from '../player/playback-controller.svelte';
	import SubtitleSyncControl from './SubtitleSyncControl.svelte';
	import ActivityBridge from '../chrome/ActivityBridge.svelte';
	import { createAutoHideChrome } from '../chrome/auto-hide.ts';
	import '../chrome/chrome.css';

	interface Props {
		itemId: string;
		/** Resume point in milliseconds, from the item's watch state. */
		startAtMs?: number;
		title: string;
		/** Fired once when playback reaches the end of the item. */
		onEnded?: () => void;
	}

	let { itemId, startAtMs = 0, title, onEnded }: Props = $props();

	const DEBUG_SEEK =
		typeof window !== 'undefined' &&
		new URLSearchParams(window.location.search).has('debugseek');

	const controller = new PlaybackController({
		itemId,
		startAtMs,
		debug: DEBUG_SEEK,
		onEnded
	});

	let containerEl = $state<HTMLDivElement | null>(null);
	let videoEl = $state<HTMLVideoElement | null>(null);
	let seekbarEl = $state<HTMLDivElement | null>(null);

	let showSubSync = $state(false);
	let showModeBadge = $state(false);
	let transcodeStats = $state<{ fps: number | null; speed: string | null; is_paused: boolean } | null>(
		null
	);
	let openMenu = $state<'audio' | 'subtitle' | 'speed' | 'quality' | 'settings' | null>(null);
	let chromeVisible = $state(true);
	let isFullscreen = $state(false);
	let isPip = $state(false);
	let isHoveringSeekbar = $state(false);
	let hoverTime = $state(0);
	let hoverPct = $state(0);
	let focusTaken = false;
	let attached = false;
	let lastItemId = itemId;

	const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 2] as const;
	const QUALITY_OPTIONS: { value: QualityLevel; label: string; detail: string }[] = [
		{ value: 'auto', label: 'Auto', detail: 'Best available' },
		{ value: 'sd', label: 'SD', detail: 'up to 480p' },
		{ value: 'hd', label: 'HD', detail: 'up to 720p' },
		{ value: 'fhd', label: 'FHD', detail: 'up to 1080p' },
		{ value: '4k', label: '4K', detail: 'up to 2160p' }
	];

	const COMPACT_MEDIA_QUERY = '(max-width: 720px)';
	let isCompact = $state(
		typeof window !== 'undefined' && window.matchMedia(COMPACT_MEDIA_QUERY).matches
	);
	$effect(() => {
		if (typeof window === 'undefined') return;
		const query = window.matchMedia(COMPACT_MEDIA_QUERY);
		const update = () => (isCompact = query.matches);
		update();
		query.addEventListener('change', update);
		return () => query.removeEventListener('change', update);
	});
	$effect(() => {
		if (!isCompact && openMenu === 'settings') openMenu = null;
	});

	const info = $derived(controller.info);
	const loadError = $derived(controller.loadError);
	const loading = $derived(controller.loading);
	const isWaiting = $derived(controller.isWaiting);
	const isPaused = $derived(controller.isPaused);
	const currentTime = $derived(controller.currentTime);
	const duration = $derived(controller.duration);
	const progressPct = $derived(controller.progressPct);
	const bufferedRanges = $derived(controller.bufferedRanges);
	const volume = $derived(controller.volume);
	const isMuted = $derived(controller.isMuted);
	const playbackRate = $derived(controller.playbackRate);
	const quality = $derived(controller.quality);
	const streamStats = $derived(controller.streamStats);
	const audioTrackIndex = $derived(controller.audioTrackIndex);
	const subtitleChoice = $derived(controller.subtitleChoice);
	const currentSubOffset = $derived(controller.currentSubOffset);
	const nowMs = $derived(controller.nowMs);
	const subtitleManager = $derived(controller.subtitleManager);

	const availableAudio = $derived(info?.audio_streams ?? []);
	const availableSubtitles = $derived(info?.subtitles ?? []);
	const availableEmbeddedSubtitles = $derived(info?.embedded_subtitles ?? []);

	const chrome = createAutoHideChrome({
		persist: () => isPaused || loading || !!loadError || !!openMenu,
		onChange: (visible) => {
			chromeVisible = visible;
		}
	});
	$effect(() => () => chrome.dispose());

	$effect(() => {
		void isPaused;
		chrome.reveal();
	});

	function toggleVideoControls(): void {
		if (isPaused) {
			chrome.reveal();
			return;
		}
		chrome.toggle();
	}

	function toggleMenu(menu: 'audio' | 'subtitle' | 'speed' | 'quality' | 'settings'): void {
		openMenu = openMenu === menu ? null : menu;
		chrome.reveal();
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

	function formatBitrate(bitsPerSecond: number): string {
		if (!Number.isFinite(bitsPerSecond) || bitsPerSecond <= 0) return '';
		const mbps = bitsPerSecond / 1_000_000;
		return mbps >= 1 ? `${mbps.toFixed(1)} Mbps` : `${Math.round(bitsPerSecond / 1000)} kbps`;
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

	const streamBitrate = $derived.by(() => {
		if (streamStats?.levelBitrate) return streamStats.levelBitrate;
		const planBitrate = info?.plan?.target_bitrate;
		if (planBitrate) return planBitrate;
		const videoBitrate = info?.video_stream?.bit_rate ?? 0;
		const audioBitrate = activeAudioStream()?.bit_rate ?? 0;
		const probed = videoBitrate + audioBitrate;
		if (probed > 0) return probed;
		const sizeBytes = info?.file?.size_bytes ?? 0;
		const durationSecs = info?.file?.duration_seconds ?? 0;
		if (sizeBytes > 0 && durationSecs > 0) return (sizeBytes * 8) / durationSecs;
		return 0;
	});

	const streamThroughput = $derived(streamStats?.measuredBitrate ?? 0);

	const endsAt = $derived.by(() => {
		if (duration <= 0) return '';
		const rate = playbackRate > 0 ? playbackRate : 1;
		const remainingSecs = Math.max(0, duration - currentTime) / rate;
		const finish = new Date(nowMs + remainingSecs * 1000);
		return finish.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
	});

	const togglePlay = () => controller.togglePlay();
	const seekBy = (deltaSeconds: number) => {
		controller.seekBy(deltaSeconds);
		chrome.reveal();
	};
	const handleSeek = (targetSeconds: number) => controller.seek(targetSeconds);
	const toggleMute = () => controller.toggleMute();
	const handleVolumeInput = (newVol: number) => controller.setVolume(newVol);
	const handleOffsetSyncChange = (offsetSeconds: number) => controller.setSubOffset(offsetSeconds);

	function selectAudioTrack(value: string): void {
		controller.selectAudioTrack(value);
		openMenu = null;
	}

	function selectSubtitle(value: string): void {
		controller.selectSubtitle(value);
		openMenu = null;
	}

	function selectPlaybackRate(rate: number): void {
		controller.setPlaybackRate(rate);
		openMenu = null;
	}

	function selectQuality(value: QualityLevel): void {
		controller.selectQuality(value);
		openMenu = null;
	}

	function handleSeekbarClick(e: MouseEvent): void {
		if (!seekbarEl || duration <= 0) return;
		const rect = seekbarEl.getBoundingClientRect();
		const clickX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
		handleSeek((clickX / rect.width) * duration);
	}

	function handleSeekbarMouseMove(e: MouseEvent): void {
		if (!seekbarEl || duration <= 0) return;
		const rect = seekbarEl.getBoundingClientRect();
		const hoverX = Math.max(0, Math.min(rect.width, e.clientX - rect.left));
		hoverPct = (hoverX / rect.width) * 100;
		hoverTime = (hoverX / rect.width) * duration;
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
		const targetTag = (event.target as HTMLElement)?.tagName?.toLowerCase();
		if (targetTag === 'input' && (event.target as HTMLInputElement).type !== 'range') {
			return;
		}
		chrome.reveal();

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
		if (videoEl && !attached) {
			attached = true;
			lastItemId = itemId;
			controller.attachVideo(videoEl);
		}
	});

	$effect(() => {
		if (attached && itemId !== lastItemId) {
			lastItemId = itemId;
			controller.switchItem(itemId, startAtMs);
		}
	});

	$effect(() => {
		const el = videoEl;
		if (!el || focusTaken) return;
		focusTaken = true;
		el.focus({ preventScroll: true });
	});

	$effect(() => {
		const handleFsChange = () => {
			isFullscreen = Boolean(document.fullscreenElement);
			controller.resizeSubtitles();
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

	$effect(() => {
		const onPageHide = () => {
			void controller.report('paused');
			controller.sendBeaconStop();
		};
		const onVisibilityChange = () => {
			if (document.visibilityState === 'hidden') {
				void controller.report('paused');
			}
		};
		window.addEventListener('pagehide', onPageHide);
		document.addEventListener('visibilitychange', onVisibilityChange);
		return () => {
			window.removeEventListener('pagehide', onPageHide);
			document.removeEventListener('visibilitychange', onVisibilityChange);
		};
	});

	$effect(() => () => controller.destroy());

	$effect(() => {
		const sid = controller.sessionId;
		const active = showModeBadge && info?.mode === 'transcode_hls' && Boolean(sid);
		if (!active || !sid) {
			transcodeStats = null;
			return;
		}
		let cancelled = false;
		const fetchStats = async () => {
			try {
				const res = await api.get<{
					fps: number | null;
					speed: string | null;
					is_paused: boolean;
				}>(`/hls/${sid}/stats`);
				if (!cancelled) transcodeStats = res;
			} catch {
				/* ignore transient stats errors */
			}
		};
		void fetchStats();
		const timer = setInterval(() => void fetchStats(), 2000);
		return () => {
			cancelled = true;
			clearInterval(timer);
		};
	});
</script>

<ActivityBridge onactivity={chrome.reveal} />
<svelte:window onkeydown={handleKeydown} />

<div
	class="player media-chrome-host"
	data-chrome-hidden={!chromeVisible}
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
			onclick={toggleVideoControls}
		></video>
	</div>

	<!-- Top Overlay Navigation Bar -->
	<div
		class={['top-overlay', 'media-chrome', 'media-chrome--top']}
		data-hidden={!chromeVisible}
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
		class={['bottom-overlay', 'media-chrome', 'media-chrome--bottom']}
		data-hidden={!chromeVisible}
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
						: openMenu === 'quality'
							? 'Playback quality'
							: openMenu === 'settings'
								? 'Player settings'
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
				{:else if openMenu === 'quality'}
					<div class="menu-heading" role="presentation">Quality</div>
					{#each QUALITY_OPTIONS as option (option.value)}
						<button
							type="button"
							role="menuitemradio"
							aria-checked={quality === option.value}
							class="menu-item"
							class:selected={quality === option.value}
							onclick={() => selectQuality(option.value)}
						>
							<span>{option.label}</span>
							<span class="menu-item-detail">{option.detail}</span>
						</button>
					{/each}
				{:else if openMenu === 'settings'}
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
					<div class="menu-heading" role="presentation">Quality</div>
					{#each QUALITY_OPTIONS as option (option.value)}
						<button
							type="button"
							role="menuitemradio"
							aria-checked={quality === option.value}
							class="menu-item"
							class:selected={quality === option.value}
							onclick={() => selectQuality(option.value)}
						>
							<span>{option.label}</span>
							<span class="menu-item-detail">{option.detail}</span>
						</button>
					{/each}
					<div class="menu-heading" role="presentation">More</div>
					<button
						type="button"
						role="menuitem"
						class="menu-item"
						onclick={() => {
							openMenu = null;
							showSubSync = true;
						}}
					>
						Subtitle sync
					</button>
					{#if info}
						<button
							type="button"
							role="menuitemcheckbox"
							aria-checked={showModeBadge}
							class="menu-item"
							class:selected={showModeBadge}
							onclick={() => {
								showModeBadge = !showModeBadge;
								openMenu = null;
							}}
						>
							Stream info
						</button>
					{/if}
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

				<div class="controls-pill">
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
				</div>

				{#if !isCompact}
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
				{/if}
			</div>

			{#if endsAt}
				<span class="end-time" title="Ends at {endsAt}">End at {endsAt}</span>
			{/if}

			<div class="controls-right">
				<div class="controls-pill">
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

					{#if !isCompact}
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
				{/if}

				{#if !isCompact}
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

					<!-- Quality selector -->
					<button
						type="button"
						class="icon-btn"
						class:active={openMenu === 'quality' || quality !== 'auto'}
						onclick={() => toggleMenu('quality')}
						title="Playback quality"
						aria-label="Playback quality"
						aria-haspopup="menu"
						aria-expanded={openMenu === 'quality'}
					>
						<MonitorPlay size={18} />
					</button>

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
				{:else}
					<!-- Settings groups speed, quality, subtitle sync and info -->
					<button
						type="button"
						class="icon-btn"
						class:active={openMenu === 'settings'}
						onclick={() => toggleMenu('settings')}
						title="Player settings"
						aria-label="Player settings"
						aria-haspopup="menu"
						aria-expanded={openMenu === 'settings'}
					>
						<Settings2 size={18} />
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
			class:hidden={!chromeVisible}
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
			{#if info.mode === 'transcode_hls'}
				<span class="mode-line">
					{#if transcodeStats?.is_paused}
						Transcode: paused
					{:else if transcodeStats?.fps != null}
						Transcode: {transcodeStats.fps.toFixed(1)} fps{#if transcodeStats.speed}
							({transcodeStats.speed})
						{/if}
					{:else}
						Transcode: starting…
					{/if}
				</span>
			{/if}
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

	/* Chrome positioning, fade and hidden state come from the shared stylesheet;
	   the video player uses pill backgrounds on controls/text instead of a gradient. */
	.top-overlay {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		padding: 1rem 1.5rem;
		background: none;
	}

	.top-left {
		display: flex;
		align-items: center;
		gap: 0.75rem;
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
		background: rgba(15, 18, 24, 0.58);
		border: none;
		color: #fff;
		cursor: pointer;
		text-decoration: none;
		flex-shrink: 0;
		backdrop-filter: blur(8px);
		transition:
			background 0.15s ease,
			color 0.15s ease;
	}

	.back-btn:hover {
		background: rgba(255, 255, 255, 0.2);
		color: var(--accent);
		text-decoration: none;
	}

	.player-title {
		font-size: 1.05rem;
		font-weight: 600;
		color: #fff;
		margin: 0;
		padding: 0.4rem 0.9rem;
		border-radius: 9999px;
		background: rgba(15, 18, 24, 0.58);
		backdrop-filter: blur(8px);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.bottom-overlay {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		padding: 1.5rem 1.5rem 1rem;
		background: none;
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

	.controls-pill {
		display: inline-flex;
		align-items: center;
		gap: 0.15rem;
		background: rgba(15, 18, 24, 0.58);
		backdrop-filter: blur(8px);
		border-radius: 9999px;
		padding: 0.2rem 0.35rem;
	}

	.icon-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		background: transparent;
		border: none;
		color: #fff;
		padding: 0.42rem;
		border-radius: 9999px;
		cursor: pointer;
		transition:
			background 0.15s ease,
			color 0.15s ease;
	}

	.icon-btn:hover {
		background: rgba(255, 255, 255, 0.18);
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
		width: 2.5rem;
		height: 2.5rem;
		padding: 0;
		background: rgba(15, 18, 24, 0.58);
		backdrop-filter: blur(8px);
	}

	.play-btn:hover {
		background: rgba(255, 255, 255, 0.2);
	}

	.end-time {
		flex: 0 0 auto;
		font-size: 0.8rem;
		font-family: ui-monospace, monospace;
		color: #fff;
		background: rgba(15, 18, 24, 0.58);
		backdrop-filter: blur(8px);
		padding: 0.35rem 0.75rem;
		border-radius: 9999px;
		white-space: nowrap;
		font-variant-numeric: tabular-nums;
		text-align: center;
	}

	.volume-group {
		display: flex;
		align-items: center;
		gap: 0.25rem;
		background: rgba(15, 18, 24, 0.58);
		backdrop-filter: blur(8px);
		border-radius: 9999px;
		padding: 0.2rem;
		transition: padding-right 0.18s ease;
	}

	.volume-group:hover,
	.volume-group:focus-within {
		padding-right: 0.65rem;
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
		color: #fff;
		background: rgba(15, 18, 24, 0.58);
		backdrop-filter: blur(8px);
		padding: 0.2rem 0.6rem;
		border-radius: 9999px;
		white-space: nowrap;
		font-variant-numeric: tabular-nums;
	}

	.time-remaining {
		color: rgba(255, 255, 255, 0.85);
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

	.menu-item-detail {
		margin-left: 0.5rem;
		color: var(--muted);
		font-size: 0.75rem;
		font-weight: 400;
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

	@media (max-width: 480px) {
		/* Very narrow screens need the full row for transport controls. */
		.end-time {
			display: none;
		}
	}
</style>
