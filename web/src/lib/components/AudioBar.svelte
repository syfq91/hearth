<script lang="ts">
	import {
		player,
		togglePlay,
		stop,
		playNext,
		playPrevious,
		nextTrackSrc,
		cycleRepeat,
		toggleShuffle,
		toggleFavorite
	} from '../player.svelte';
	import { ReplayGainGraph, replayGainLinear } from '../player/replaygain';
	import { withMediaToken } from '../media-token.svelte';
	import {
		playerUi,
		openFullPlayer,
		toggleMute,
		rememberVolume,
		formatTime,
		loadReplayGain
	} from '../player/ui.svelte';
	import {
		Play,
		Pause,
		SkipBack,
		SkipForward,
		Square,
		Volume2,
		Volume1,
		VolumeX,
		Music,
		Repeat,
		Repeat1,
		Shuffle,
		Heart,
		Maximize2
	} from '@lucide/svelte';

	let { readerOverlay = false }: { readerOverlay?: boolean } = $props();

	let audio = $state<HTMLAudioElement | null>(null);

	// ReplayGain runs through a Web Audio gain node so quiet tracks can be
	// boosted above 100%; the element's own volume stays the user's control.
	// Web Audio routing prevents background playback on mobile browsers, so it
	// is opt-in and loaded before the element mounts (see the <audio> block).
	const replayGain = new ReplayGainGraph();
	loadReplayGain();

	// Artwork is URL-based media, so it needs a media token like the stream.
	// Store it in shared UI state so the full-page player shows the same art.
	$effect(() => {
		const track = player.track;
		const artwork = track?.artworkUrl;
		if (!track || !artwork) {
			playerUi.artworkSrc = null;
			return;
		}
		let cancelled = false;
		void withMediaToken(artwork)
			.then((url) => {
				if (!cancelled && player.track?.id === track.id) playerUi.artworkSrc = url;
			})
			.catch(() => {
				/* artwork is decorative */
			});
		return () => {
			cancelled = true;
		};
	});

	// Drive the element from the store: a new src starts playback, and the
	// toggle button reflects (and controls) the real element state.
	$effect(() => {
		const element = audio;
		const src = player.src;
		const shouldPlay = player.playing;
		if (!element) return;
		if (!src) {
			// Cleared (stopped, or restarting the current track): release the
			// resource so the next src really reloads from the top.
			if (element.getAttribute('src')) {
				element.pause();
				element.removeAttribute('src');
				element.load();
			}
			return;
		}
		if (element.src !== new URL(src, window.location.origin).href) {
			element.src = src;
			if (shouldPlay) void element.play().catch(() => {
				/* autoplay blocked until the user interacts */
			});
			return;
		}
		if (shouldPlay && element.paused) void element.play().catch(() => {});
		if (!shouldPlay && !element.paused) element.pause();
	});

	// Gapless attempt: warm the next queued track's stream so the handoff on
	// `ended` only has to swap the element's src.
	let preloader: HTMLAudioElement | null = null;

	$effect(() => {
		const index = player.index;
		void (async () => {
			const src = await nextTrackSrc();
			if (!src || player.index !== index) return;
			const next = new Audio();
			next.preload = 'metadata';
			next.src = src;
			preloader?.pause();
			preloader?.removeAttribute('src');
			preloader = next;
		})();
	});

	// Media Session: OS-level metadata, playback state and media-key actions.
	$effect(() => {
		if (!('mediaSession' in navigator)) return;
		const track = player.track;
		if (!track) {
			navigator.mediaSession.metadata = null;
			return;
		}
		navigator.mediaSession.metadata = new MediaMetadata({
			title: track.title,
			artist: track.artistName ?? '',
			album: track.albumTitle ?? ''
		});
		const artwork = track.artworkUrl;
		if (artwork) {
			void withMediaToken(artwork)
				.then((url) => {
					if (player.track?.id !== track.id || !('mediaSession' in navigator)) return;
					navigator.mediaSession.metadata = new MediaMetadata({
						title: track.title,
						artist: track.artistName ?? '',
						album: track.albumTitle ?? '',
						artwork: [{ src: url }]
					});
				})
				.catch(() => {
					/* artwork is decorative */
				});
		}
	});

	$effect(() => {
		if ('mediaSession' in navigator) {
			navigator.mediaSession.playbackState = player.playing ? 'playing' : 'paused';
		}
	});

	$effect(() => {
		if (!('mediaSession' in navigator)) return;
		const mediaSession = navigator.mediaSession;
		const set = (action: MediaSessionAction, handler: MediaSessionActionHandler | null) => {
			try {
				mediaSession.setActionHandler(action, handler);
			} catch {
				/* action unsupported on this browser */
			}
		};
		set('play', () => {
			replayGain.resume();
			player.playing = true;
		});
		set('pause', () => {
			player.playing = false;
		});
		set('nexttrack', () => void playNext());
		set('previoustrack', () => void playPrevious());
		set('seekto', (details) => {
			const element = audio;
			if (element && details.seekTime != null && Number.isFinite(details.seekTime)) {
				element.currentTime = details.seekTime;
			}
		});
		return () => {
			set('play', null);
			set('pause', null);
			set('nexttrack', null);
			set('previoustrack', null);
			set('seekto', null);
		};
	});

	// Only route the persistent element through Web Audio when leveling is
	// enabled. With the preference off the element uses the browser's normal
	// media output, which is what keeps music playing in the background.
	$effect(() => {
		const element = audio;
		if (element && playerUi.replayGain) replayGain.connect(element);
		else replayGain.disconnect();
	});

	$effect(() => {
		replayGain.setLinear(replayGainLinear(player.track));
	});

	function handlePlay(): void {
		// AudioContext starts suspended until a user gesture; playback starts
		// from a click or a media key, both of which allow a resume here.
		replayGain.resume();
		player.playing = true;
	}

	// Opt-in leveling still needs help after the browser backgrounded the page:
	// resume the context when the tab returns, and restart the element if it was
	// paused by the OS while never changing the player's own play intent.
	function resumePlayback(): void {
		replayGain.resume();
		const element = audio;
		if (player.playing && element && element.paused) {
			void element.play().catch(() => {});
		}
	}

	function handleVisibilityChange(): void {
		if (document.visibilityState === 'visible') resumePlayback();
	}

	function handleEnded(): void {
		if (player.repeat === 'one' && audio) {
			audio.currentTime = 0;
			void audio.play().catch(() => {});
			return;
		}
		void playNext();
	}

	function handleTimeUpdate(): void {
		if (!('mediaSession' in navigator) || !audio) return;
		const duration = audio.duration;
		if (!Number.isFinite(duration) || duration <= 0) return;
		try {
			navigator.mediaSession.setPositionState({
				duration,
				position: Math.min(Math.max(0, audio.currentTime), duration),
				playbackRate: audio.playbackRate || 1
			});
		} catch {
			/* some browsers throw for unusual values */
		}
	}

	// Load persisted volume on client
	$effect(() => {
		if (typeof localStorage !== 'undefined') {
			const saved = localStorage.getItem('hearth.audio_volume');
			if (saved !== null) {
				const parsed = Number.parseFloat(saved);
				if (Number.isFinite(parsed) && parsed >= 0 && parsed <= 1) {
					playerUi.volume = parsed;
					rememberVolume(parsed);
				}
			}
		}
	});

	// Keep the audio element in sync immediately; persist after the slider
	// settles instead of writing localStorage on every tick. This is the user's
	// volume; ReplayGain is applied separately by the gain node above.
	$effect(() => {
		const nextVolume = playerUi.volume;
		if (audio) {
			audio.volume = nextVolume;
		}
		const timer = setTimeout(() => {
			if (typeof localStorage !== 'undefined') {
				localStorage.setItem('hearth.audio_volume', String(nextVolume));
			}
		}, 300);
		return () => clearTimeout(timer);
	});
</script>

<svelte:document onvisibilitychange={handleVisibilityChange} />
<svelte:window onpageshow={resumePlayback} />

{#if player.track}
	<div
		class="bar"
		class:overlay={readerOverlay}
		class:chrome-hidden={readerOverlay && !playerUi.readerChromeVisible}
		role="region"
		aria-label="Now playing"
	>
		<div class="seek-row">
			<input
				type="range"
				class="seek"
				min="0"
				max={playerUi.duration > 0 ? playerUi.duration : 1}
				step="0.1"
				bind:value={playerUi.currentTime}
				disabled={!(playerUi.duration > 0)}
				aria-label="Seek"
				aria-valuetext={`${formatTime(playerUi.currentTime)} of ${formatTime(playerUi.duration)}`}
				title={formatTime(playerUi.currentTime)}
				style={`--progress: ${playerUi.duration > 0 ? Math.min(100, Math.max(0, (playerUi.currentTime / playerUi.duration) * 100)) : 0}%`}
			/>
		</div>

		<div class="bar-row">
			<button
				type="button"
				class="left"
				onclick={openFullPlayer}
				aria-label="Open full screen player"
				title="Open full screen player"
			>
				<div class="art" aria-hidden="true">
					{#if playerUi.artworkSrc}
						<img src={playerUi.artworkSrc} alt="" width="48" height="48" />
					{:else}
						<Music size={22} />
					{/if}
				</div>

				<div class="meta">
					<span class="title">{player.track.title}</span>
					{#if player.track.artistName || player.track.albumTitle}
						<span class="sub">
							{[player.track.artistName, player.track.albumTitle].filter(Boolean).join(' · ')}
						</span>
					{/if}
				</div>
				<span class="expand-icon"><Maximize2 size={15} aria-hidden="true" /></span>
			</button>

			<div class="transport">
				<div class="transport-buttons">
					<button
						type="button"
						class="ghost"
						onclick={() => void playPrevious()}
						disabled={player.index < 0}
						aria-label="Previous track"
						title="Previous track"
					>
						<SkipBack size={16} />
					</button>
					<button
						type="button"
						class="ghost"
						onclick={togglePlay}
						aria-label={player.playing ? 'Pause' : 'Play'}
						title={player.playing ? 'Pause' : 'Play'}
					>
						{#if player.playing}
							<Pause size={16} />
						{:else}
							<Play size={16} />
						{/if}
					</button>
					<button
						type="button"
						class="ghost"
						onclick={() => void playNext()}
						disabled={player.index < 0 ||
							(player.index + 1 >= player.queue.length && player.repeat !== 'all')}
						aria-label="Next track"
						title="Next track"
					>
						<SkipForward size={16} />
					</button>
					<button
						type="button"
						class="ghost"
						onclick={stop}
						aria-label="Stop playback"
						title="Stop"
					>
						<Square size={16} />
					</button>
				</div>
				<div class="transport-info" aria-hidden="true">
					{#if player.queue.length > 1}
						<span class="position">{player.index + 1}/{player.queue.length}</span>
					{/if}
					<span class="time">
						{formatTime(playerUi.currentTime)} / {formatTime(playerUi.duration)}
					</span>
				</div>
			</div>

			<div class="right">
				<div class="extra-controls">
					<button
						type="button"
						class="control-btn"
						class:active={player.repeat !== 'off'}
						onclick={() => cycleRepeat()}
						aria-label={`Repeat: ${player.repeat}`}
						title={`Repeat: ${player.repeat}`}
					>
						{#if player.repeat === 'one'}
							<Repeat1 size={16} />
						{:else}
							<Repeat size={16} />
						{/if}
					</button>
					<button
						type="button"
						class="control-btn"
						class:active={player.shuffle}
						onclick={() => toggleShuffle()}
						aria-label={player.shuffle ? 'Shuffle on' : 'Shuffle off'}
						aria-pressed={player.shuffle}
						title="Shuffle"
					>
						<Shuffle size={16} />
					</button>
					<button
						type="button"
						class="control-btn"
						class:active={player.favorite}
						onclick={() => void toggleFavorite()}
						aria-label={player.favorite ? 'Remove from favourites' : 'Add to favourites'}
						aria-pressed={player.favorite}
						title={player.favorite ? 'Remove from favourites' : 'Add to favourites'}
					>
						<Heart size={16} />
					</button>
				</div>

				<div class="volume-control">
					<button
						type="button"
						class="ghost volume-btn"
						onclick={toggleMute}
						aria-label={playerUi.volume === 0 ? 'Unmute' : 'Mute'}
						title={playerUi.volume === 0 ? 'Unmute' : 'Mute'}
					>
						{#if playerUi.volume === 0}
							<VolumeX size={16} />
						{:else if playerUi.volume < 0.5}
							<Volume1 size={16} />
						{:else}
							<Volume2 size={16} />
						{/if}
					</button>
					<input
						type="range"
						min="0"
						max="1"
						step="0.01"
						bind:value={playerUi.volume}
						oninput={(event) =>
							rememberVolume(Number.parseFloat((event.currentTarget as HTMLInputElement).value))}
						aria-label="Volume"
						aria-valuetext={`${Math.round(playerUi.volume * 100)}%`}
						title={`Volume: ${Math.round(playerUi.volume * 100)}%`}
						style={`--volume-progress: ${Math.round(playerUi.volume * 100)}%`}
						class="volume-slider"
					/>
				</div>
			</div>
		</div>

		<!-- A MediaElementAudioSourceNode belongs to one element for life: a
		     second createMediaElementSource on the same element throws. Remount
		     the element when ReplayGain is toggled so the fresh element can be
		     routed (or left on direct output); bind:currentTime restores the
		     position and player.playing resumes it. -->
		{#key playerUi.replayGain}
			<audio
				bind:this={audio}
				bind:currentTime={playerUi.currentTime}
				bind:duration={playerUi.duration}
				ontimeupdate={handleTimeUpdate}
				onplay={handlePlay}
				onpause={() => (player.playing = false)}
				onended={handleEnded}
			></audio>
		{/key}
	</div>

	{#if player.error}
		<p
			class="error"
			class:overlay={readerOverlay}
			class:chrome-hidden={readerOverlay && !playerUi.readerChromeVisible}
			role="alert"
		>
			{player.error}
		</p>
	{/if}
{/if}

<style>
	.bar {
		position: sticky;
		bottom: 0;
		display: flex;
		flex-direction: column;
		padding: 0.6rem 1.25rem;
		background: var(--surface);
		border-top: 1px solid var(--border);
		z-index: 20;
	}

	/* Reader pages: the dock floats with the header instead of taking space,
	   and fades out together with it after a page turn or a short idle. */
	.bar.overlay {
		position: fixed;
		left: 0;
		right: 0;
		z-index: 35;
		background: linear-gradient(to top, rgba(14, 17, 22, 0.96), rgba(22, 27, 34, 0.88));
		backdrop-filter: blur(6px);
		transition:
			opacity 0.2s ease,
			transform 0.2s ease;
	}

	.bar.chrome-hidden,
	.error.chrome-hidden {
		visibility: hidden;
		opacity: 0;
		transform: translateY(100%);
		pointer-events: none;
	}

	.seek-row {
		margin: -0.6rem -1.25rem 0.5rem;
	}

	.seek {
		display: block;
		width: 100%;
		height: 0.3rem;
		margin: 0;
		padding: 0;
		border: 0;
		border-radius: 0;
		appearance: none;
		-webkit-appearance: none;
		background: linear-gradient(
			to right,
			var(--accent) var(--progress, 0%),
			var(--border) var(--progress, 0%)
		);
		cursor: pointer;
	}

	.seek:disabled {
		cursor: default;
	}

	.seek::-webkit-slider-thumb {
		appearance: none;
		-webkit-appearance: none;
		width: 0.7rem;
		height: 0.7rem;
		border-radius: 50%;
		background: var(--accent);
		border: none;
	}

	.seek::-moz-range-thumb {
		width: 0.7rem;
		height: 0.7rem;
		border-radius: 50%;
		background: var(--accent);
		border: none;
	}

	.seek:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: 2px;
	}

	.bar-row {
		display: grid;
		grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
		align-items: center;
		gap: 1rem;
	}

	.left {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		min-width: 0;
		background: transparent;
		border: 1px solid transparent;
		border-radius: var(--radius);
		padding: 0.15rem;
		margin: -0.15rem;
		text-align: left;
		color: inherit;
		font: inherit;
		cursor: pointer;
	}

	.left:hover {
		background: var(--surface-2);
	}

	.left:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: 2px;
	}

	.expand-icon {
		flex: 0 0 auto;
		color: var(--muted);
		opacity: 0;
		transition: opacity 0.15s ease;
	}

	.left:hover .expand-icon,
	.left:focus-visible .expand-icon {
		opacity: 1;
	}

	.transport {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.2rem;
	}

	.transport-buttons {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.transport-info {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.right {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		min-width: 0;
	}

	.art {
		flex: 0 0 auto;
		width: 3rem;
		height: 3rem;
		border-radius: calc(var(--radius) - 2px);
		overflow: hidden;
		display: flex;
		align-items: center;
		justify-content: center;
		background: var(--surface-2);
		border: 1px solid var(--border);
		color: var(--muted);
	}

	.art img {
		display: block;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.meta {
		display: flex;
		flex-direction: column;
		min-width: 0;
		flex: 1;
	}

	.title {
		font-weight: 600;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.sub {
		color: var(--muted);
		font-size: 0.85rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.position {
		color: var(--muted);
		font-size: 0.75rem;
		font-variant-numeric: tabular-nums;
	}

	.time {
		color: var(--muted);
		font-size: 0.8rem;
		font-variant-numeric: tabular-nums;
	}

	.ghost {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		background: transparent;
		border: 1px solid transparent;
		border-radius: var(--radius);
		padding: 0.35rem 0.85rem;
		cursor: pointer;
	}

	.ghost:hover:not(:disabled) {
		background: var(--surface-2);
	}

	.ghost:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.extra-controls {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		margin-right: 0.35rem;
	}

	.control-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		background: transparent;
		border: 1px solid transparent;
		border-radius: var(--radius);
		padding: 0.35rem;
		color: var(--muted);
		cursor: pointer;
		transition:
			color 0.15s ease,
			background 0.15s ease;
	}

	.control-btn:hover {
		background: var(--surface-2);
		color: var(--text);
	}

	.control-btn.active {
		color: var(--accent);
	}

	.control-btn:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: 2px;
	}

	.volume-control {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		margin-left: 0.25rem;
	}

	.volume-btn {
		padding: 0.35rem 0.5rem;
	}

	.volume-slider {
		-webkit-appearance: none;
		appearance: none;
		width: 5.5rem;
		height: 0.35rem;
		border-radius: 999px;
		background: linear-gradient(
			to right,
			var(--accent) var(--volume-progress, 0%),
			var(--surface-2) var(--volume-progress, 0%)
		);
		border: 1px solid var(--border);
		outline: none;
		cursor: pointer;
		accent-color: var(--accent);
		margin: 0;
		padding: 0;
	}

	.volume-slider::-webkit-slider-thumb {
		-webkit-appearance: none;
		appearance: none;
		width: 0.85rem;
		height: 0.85rem;
		border-radius: 50%;
		background: var(--accent);
		cursor: pointer;
		border: none;
		transition: transform 0.1s ease;
	}

	.volume-slider::-moz-range-thumb {
		width: 0.85rem;
		height: 0.85rem;
		border-radius: 50%;
		background: var(--accent);
		cursor: pointer;
		border: none;
	}

	.volume-slider::-webkit-slider-thumb:hover {
		transform: scale(1.15);
	}

	.volume-slider:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: 3px;
	}

	.error {
		position: sticky;
		bottom: 0;
		margin: 0;
		padding: 0.4rem 1.25rem;
		background: var(--surface);
		color: var(--danger);
		border-top: 1px solid var(--border);
	}

	.error.overlay {
		position: fixed;
		left: 0;
		right: 0;
		bottom: 0;
		z-index: 36;
		transition:
			opacity 0.2s ease,
			transform 0.2s ease;
	}

	@media (max-width: 720px) {
		.bar-row {
			grid-template-columns: minmax(0, 1fr) auto;
			row-gap: 0.4rem;
		}

		.left {
			grid-column: 1;
			grid-row: 1;
		}

		.right {
			grid-column: 2;
			grid-row: 1;
		}

		.transport {
			grid-column: 1 / -1;
			grid-row: 2;
		}
	}
</style>
