<script lang="ts">
	import { player, togglePlay, stop } from '../player.svelte';

	let audio = $state<HTMLAudioElement | null>(null);

	// Drive the element from the store: a new src starts playback, and the
	// toggle button reflects (and controls) the real element state.
	$effect(() => {
		const element = audio;
		const src = player.src;
		const shouldPlay = player.playing;
		if (!element || !src) return;
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
			onplay={() => (player.playing = true)}
			onpause={() => (player.playing = false)}
			onended={() => (player.playing = false)}
		></audio>

		<div class="controls">
			<span class="time" aria-hidden="true">
				{formatTime(currentTime)} / {formatTime(duration)}
			</span>
			<button type="button" class="ghost" onclick={togglePlay}>
				{player.playing ? 'Pause' : 'Play'}
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
		align-items: center;
		gap: 1rem;
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
		gap: 0.5rem;
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

	.ghost:hover {
		background: var(--surface);
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
