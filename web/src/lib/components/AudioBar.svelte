<script lang="ts">
	import {
		player,
		togglePlay,
		stop,
		playNext,
		playPrevious,
		nextTrackSrc
	} from '../player.svelte';
	import { withMediaToken } from '../media-token.svelte';

	let audio = $state<HTMLAudioElement | null>(null);

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
			next.preload = 'auto';
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

	function formatTime(seconds: number): string {
		if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
		const m = Math.floor(seconds / 60);
		const s = Math.floor(seconds % 60);
		return `${m}:${s.toString().padStart(2, '0')}`;
	}

	let currentTime = $state(0);
	let duration = $state(0);
</script>

{#if player.track}
	<div class="bar" role="region" aria-label="Now playing">
		<div class="meta">
			<span class="title">{player.track.title}</span>
			{#if player.track.artistName || player.track.albumTitle}
				<span class="sub">
					{[player.track.artistName, player.track.albumTitle].filter(Boolean).join(' · ')}
				</span>
			{/if}
		</div>

		<!-- svelte-ignore a11y_media_has_caption -->
		<audio
			bind:this={audio}
			bind:currentTime
			bind:duration
			ontimeupdate={handleTimeUpdate}
			onplay={() => (player.playing = true)}
			onpause={() => (player.playing = false)}
			onended={() => void playNext()}
		></audio>

		<div class="controls">
			{#if player.queue.length > 1}
				<span class="position" aria-hidden="true">
					{player.index + 1}/{player.queue.length}
				</span>
			{/if}
			<span class="time" aria-hidden="true">
				{formatTime(currentTime)} / {formatTime(duration)}
			</span>
			<button
				type="button"
				class="ghost"
				onclick={() => void playPrevious()}
				disabled={player.index < 0}
				aria-label="Previous track"
			>
				⏮
			</button>
			<button type="button" class="ghost" onclick={togglePlay}>
				{player.playing ? 'Pause' : 'Play'}
			</button>
			<button
				type="button"
				class="ghost"
				onclick={() => void playNext()}
				disabled={player.index < 0 || player.index + 1 >= player.queue.length}
				aria-label="Next track"
			>
				⏭
			</button>
			<button type="button" class="ghost" onclick={stop}>Stop</button>
		</div>
	</div>

	{#if player.error}
		<p class="error" role="alert">{player.error}</p>
	{/if}
{/if}

<style>
	.bar {
		position: sticky;
		bottom: 0;
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 1rem;
		padding: 0.6rem 1.25rem;
		background: var(--surface);
		border-top: 1px solid var(--border);
		z-index: 20;
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

	.controls {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.5rem;
	}

	.position {
		color: var(--muted);
		font-size: 0.8rem;
		font-variant-numeric: tabular-nums;
	}

	.time {
		color: var(--muted);
		font-size: 0.85rem;
		font-variant-numeric: tabular-nums;
	}

	.ghost {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.35rem 0.85rem;
		cursor: pointer;
	}

	.ghost:hover:not(:disabled) {
		background: var(--surface);
	}

	.ghost:disabled {
		opacity: 0.5;
		cursor: default;
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
</style>
