<script lang="ts">
	/**
	 * Full-page music player, opened from the mini dock. It shares the audio
	 * element and all playback state with AudioBar, so opening/closing it
	 * never interrupts the music.
	 */
	import {
		player,
		togglePlay,
		stop,
		playNext,
		playPrevious,
		playAt,
		toggleFavorite,
		removeFromQueue,
		cycleRepeat,
		toggleShuffle
	} from '../player.svelte';
	import {
		playerUi,
		closeFullPlayer,
		toggleMute,
		rememberVolume,
		formatTime
	} from '../player/ui.svelte';
	import { resolveTrackArtworkSrc } from '../music-artwork';
	import {
		ChevronDown,
		X,
		Play,
		Pause,
		SkipBack,
		SkipForward,
		Repeat,
		Repeat1,
		Shuffle,
		Heart,
		Volume2,
		Volume1,
		VolumeX,
		Music,
		GripVertical,
		Trash2
	} from '@lucide/svelte';

	let overlay = $state<HTMLDivElement | null>(null);
	let minimizeButton = $state<HTMLButtonElement | null>(null);
	let previousFocus: HTMLElement | null = null;
	/** Tokenized cover per queued track id, resolved independently of the hero art. */
	let queueArtwork = $state<Record<string, string>>({});

	$effect(() => {
		const queue = player.queue;
		let cancelled = false;
		void (async () => {
			const next: Record<string, string> = {};
			await Promise.all(
				queue.map(async (track) => {
					const src = await resolveTrackArtworkSrc(track);
					if (src && !cancelled) next[track.id] = src;
				})
			);
			if (!cancelled) queueArtwork = next;
		})();
		return () => {
			cancelled = true;
		};
	});

	// Stopping playback removes the audio element and hides this overlay; make
	// sure the next play starts in the mini dock instead of reopening it.
	$effect(() => {
		if (playerUi.expanded && !player.track) closeFullPlayer();
	});

	$effect(() => {
		if (playerUi.expanded) {
			previousFocus =
				document.activeElement instanceof HTMLElement ? document.activeElement : null;
			minimizeButton?.focus();
		} else if (previousFocus) {
			previousFocus.focus();
			previousFocus = null;
		}
	});

	function stopAndClose(): void {
		stop();
		closeFullPlayer();
	}

	function handleKeydown(event: KeyboardEvent): void {
		if (!playerUi.expanded) return;
		if (event.key === 'Escape') {
			event.preventDefault();
			closeFullPlayer();
			return;
		}
		if (event.key !== 'Tab' || !overlay) return;
		const focusable = overlay.querySelectorAll<HTMLElement>(
			'button:not([disabled]), input:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
		);
		if (focusable.length === 0) return;
		const first = focusable[0];
		const last = focusable[focusable.length - 1];
		const active = document.activeElement;
		if (event.shiftKey && (active === first || !overlay.contains(active))) {
			event.preventDefault();
			last.focus();
		} else if (!event.shiftKey && (active === last || !overlay.contains(active))) {
			event.preventDefault();
			first.focus();
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} />

{#if playerUi.expanded && player.track}
	<div
		class="overlay"
		role="dialog"
		aria-modal="true"
		aria-label="Full screen player"
		bind:this={overlay}
	>
		<header class="topbar">
			<button
				type="button"
				class="icon-btn"
				bind:this={minimizeButton}
				onclick={closeFullPlayer}
				aria-label="Minimize player"
				title="Minimize (Esc)"
			>
				<ChevronDown size={20} />
			</button>
			<span class="topbar-label">Now playing</span>
			<span class="topbar-spacer"></span>
			<button
				type="button"
				class="icon-btn"
				onclick={stopAndClose}
				aria-label="Stop playback"
				title="Stop playback"
			>
				<X size={20} />
			</button>
		</header>

		<div class="content">
			<section class="hero" aria-label="Currently playing">
				<div class="art" aria-hidden="true">
					{#if playerUi.artworkSrc}
						<img src={playerUi.artworkSrc} alt="" />
					{:else}
						<Music size={64} />
					{/if}
				</div>

				<div class="details">
					<div class="title-row">
						<div class="titles">
							<h1>{player.track.title}</h1>
							<p class="sub">
								{[player.track.artistName, player.track.albumTitle].filter(Boolean).join(' · ')}
							</p>
						</div>
						<button
							type="button"
							class="icon-btn fav-btn title-fav"
							class:active={player.favorite}
							onclick={() => void toggleFavorite()}
							aria-label={player.favorite ? 'Remove from favourites' : 'Add to favourites'}
							aria-pressed={player.favorite}
							title={player.favorite ? 'Remove from favourites' : 'Add to favourites'}
						>
							<Heart size={20} fill={player.favorite ? 'currentColor' : 'none'} />
						</button>
					</div>

					<div class="seek-row">
						<span class="time">{formatTime(playerUi.currentTime)}</span>
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
							style={`--progress: ${playerUi.duration > 0 ? Math.min(100, Math.max(0, (playerUi.currentTime / playerUi.duration) * 100)) : 0}%`}
						/>
						<span class="time">{formatTime(playerUi.duration)}</span>
					</div>

					<div class="controls-row">
						<div class="transport">
							<button
								type="button"
								class="icon-btn"
								class:active={player.repeat !== 'off'}
								onclick={() => cycleRepeat()}
								aria-label={`Repeat: ${player.repeat}`}
								title={`Repeat: ${player.repeat}`}
							>
								{#if player.repeat === 'one'}
									<Repeat1 size={20} />
								{:else}
									<Repeat size={20} />
								{/if}
							</button>
							<button
								type="button"
								class="icon-btn"
								onclick={() => void playPrevious()}
								disabled={player.index < 0}
								aria-label="Previous track"
								title="Previous track"
							>
								<SkipBack size={22} fill="currentColor" />
							</button>
							<button
								type="button"
								class="play-btn"
								onclick={togglePlay}
								aria-label={player.playing ? 'Pause' : 'Play'}
								title={player.playing ? 'Pause' : 'Play'}
							>
								{#if player.playing}
									<Pause size={26} fill="currentColor" />
								{:else}
									<Play size={26} fill="currentColor" />
								{/if}
							</button>
							<button
								type="button"
								class="icon-btn"
								onclick={() => void playNext()}
								disabled={player.index < 0 ||
									(player.index + 1 >= player.queue.length && player.repeat !== 'all')}
								aria-label="Next track"
								title="Next track"
							>
								<SkipForward size={22} fill="currentColor" />
							</button>
							<button
								type="button"
								class="icon-btn"
								class:active={player.shuffle}
								onclick={() => toggleShuffle()}
								aria-label={player.shuffle ? 'Shuffle on' : 'Shuffle off'}
								aria-pressed={player.shuffle}
								title="Shuffle"
							>
								<Shuffle size={20} />
							</button>
						</div>

						<div class="extras">
							<button
								type="button"
								class="icon-btn fav-btn controls-fav"
								class:active={player.favorite}
								onclick={() => void toggleFavorite()}
								aria-label={player.favorite ? 'Remove from favourites' : 'Add to favourites'}
								aria-pressed={player.favorite}
								title={player.favorite ? 'Remove from favourites' : 'Add to favourites'}
							>
								<Heart size={20} fill={player.favorite ? 'currentColor' : 'none'} />
							</button>
							<div class="volume-control">
								<button
									type="button"
									class="icon-btn"
									onclick={toggleMute}
									aria-label={playerUi.volume === 0 ? 'Unmute' : 'Mute'}
									title={playerUi.volume === 0 ? 'Unmute' : 'Mute'}
								>
									{#if playerUi.volume === 0}
										<VolumeX size={20} />
									{:else if playerUi.volume < 0.5}
										<Volume1 size={20} />
									{:else}
										<Volume2 size={20} />
									{/if}
								</button>
								<input
									type="range"
									class="volume-slider"
									min="0"
									max="1"
									step="0.01"
									bind:value={playerUi.volume}
									oninput={(event) =>
										rememberVolume(
											Number.parseFloat((event.currentTarget as HTMLInputElement).value)
										)}
									aria-label="Volume"
									aria-valuetext={`${Math.round(playerUi.volume * 100)}%`}
									title={`Volume: ${Math.round(playerUi.volume * 100)}%`}
									style={`--volume-progress: ${Math.round(playerUi.volume * 100)}%`}
								/>
							</div>
						</div>
					</div>
				</div>
			</section>

			{#if player.queue.length > 0}
				<section class="queue" aria-label="Play queue">
					<h2>Queue <span class="count">{player.queue.length}</span></h2>
					<ul>
						{#each player.queue as track, index (`${track.id}:${index}`)}
							<li class:current={index === player.index}>
								<span class="grip"><GripVertical size={16} aria-hidden="true" /></span>
								<button
									type="button"
									class="track-main"
									onclick={() => void playAt(index)}
									aria-label={`Play ${track.title}`}
								>
									<span class="thumb" aria-hidden="true">
										{#if queueArtwork[track.id]}
											<img src={queueArtwork[track.id]} alt="" />
										{:else}
											<Music size={14} />
										{/if}
									</span>
									<span class="track-title">{track.title}</span>
									{#if track.artistName}
										<span class="track-artist">{track.artistName}</span>
									{/if}
									{#if track.durationSeconds && track.durationSeconds > 0}
										<span class="track-duration">{formatTime(track.durationSeconds)}</span>
									{/if}
								</button>
								<button
									type="button"
									class="icon-btn row-btn"
									class:active={track.favorite}
									onclick={() => void toggleFavorite(track)}
									aria-label={track.favorite
										? `Remove ${track.title} from favourites`
										: `Add ${track.title} to favourites`}
									title="Favourite"
								>
									<Heart size={17} fill={track.favorite ? 'currentColor' : 'none'} />
								</button>
								<button
									type="button"
									class="icon-btn row-btn"
									onclick={() => removeFromQueue(index)}
									aria-label={`Remove ${track.title} from queue`}
									title="Remove from queue"
								>
									<Trash2 size={17} />
								</button>
							</li>
						{/each}
					</ul>
				</section>
			{/if}
		</div>
	</div>
{/if}

<style>
	.overlay {
		position: fixed;
		inset: 0;
		z-index: 100;
		overflow-y: auto;
		background: var(--bg);
	}

	.topbar {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.75rem 1.5rem;
		border-bottom: 1px solid var(--border);
	}

	.topbar-label {
		font-weight: 600;
		color: var(--muted);
	}

	.topbar-spacer {
		flex: 1;
	}

	.icon-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		background: transparent;
		border: 1px solid transparent;
		border-radius: var(--radius);
		padding: 0.4rem;
		color: var(--muted);
		cursor: pointer;
	}

	.icon-btn:hover:not(:disabled),
	.icon-btn.active {
		color: var(--accent);
	}

	.icon-btn:disabled {
		opacity: 0.4;
		cursor: default;
	}

	.icon-btn:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: 2px;
	}

	.content {
		max-width: 1600px;
		margin: 0 auto;
		padding: 1.5rem;
	}

	.hero {
		display: flex;
		align-items: flex-start;
		gap: 2rem;
		padding-bottom: 1.5rem;
		border-bottom: 1px solid var(--border);
	}

	.art {
		flex: 0 0 auto;
		width: clamp(180px, 22vw, 300px);
		aspect-ratio: 1;
		border-radius: var(--radius);
		overflow: hidden;
		display: flex;
		align-items: center;
		justify-content: center;
		background: var(--surface);
		color: var(--muted);
		box-shadow: 0 8px 28px rgba(0, 0, 0, 0.4);
	}

	.art img {
		display: block;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.details {
		flex: 1;
		min-width: 0;
	}

	.title-row {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
	}

	.titles {
		min-width: 0;
	}

	.fav-btn {
		flex: 0 0 auto;
	}

	/* One favourite control, two placements: beside the volume on wide
	   screens, beside the track details on narrow ones. */
	.title-fav {
		display: none;
	}

	.controls-fav {
		display: inline-flex;
	}

	.details h1 {
		font-size: clamp(1.5rem, 2.5vw, 2.25rem);
		margin: 0 0 0.25rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.sub {
		color: var(--muted);
		margin: 0 0 1.25rem;
	}

	.seek-row {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-bottom: 1rem;
	}

	.time {
		color: var(--muted);
		font-size: 0.85rem;
		font-variant-numeric: tabular-nums;
		min-width: 3.5rem;
		text-align: center;
	}

	.seek,
	.volume-slider {
		appearance: none;
		-webkit-appearance: none;
		height: 0.35rem;
		border-radius: 999px;
		outline: none;
		cursor: pointer;
		margin: 0;
		padding: 0;
		border: 0;
	}

	.seek {
		flex: 1;
		background: linear-gradient(
			to right,
			var(--accent) var(--progress, 0%),
			var(--border) var(--progress, 0%)
		);
	}

	.seek::-webkit-slider-thumb,
	.volume-slider::-webkit-slider-thumb {
		appearance: none;
		-webkit-appearance: none;
		width: 0.85rem;
		height: 0.85rem;
		border-radius: 50%;
		background: var(--accent);
		border: none;
	}

	.seek::-moz-range-thumb,
	.volume-slider::-moz-range-thumb {
		width: 0.85rem;
		height: 0.85rem;
		border-radius: 50%;
		background: var(--accent);
		border: none;
	}

	.seek:focus-visible,
	.volume-slider:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: 3px;
	}

	.controls-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		flex-wrap: wrap;
	}

	.transport,
	.extras {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.play-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 3rem;
		height: 3rem;
		border-radius: 50%;
		background: var(--accent);
		color: var(--accent-contrast);
		border: none;
		cursor: pointer;
	}

	.play-btn:hover {
		filter: brightness(1.1);
	}

	.play-btn:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: 3px;
	}

	.volume-control {
		display: flex;
		align-items: center;
		gap: 0.25rem;
	}

	.volume-slider {
		width: 6rem;
		background: linear-gradient(
			to right,
			var(--accent) var(--volume-progress, 0%),
			var(--surface-2) var(--volume-progress, 0%)
		);
	}

	.queue h2 {
		font-size: 1rem;
		color: var(--muted);
		margin: 1rem 0 0.5rem;
	}

	.count {
		color: var(--text);
	}

	.queue ul {
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.queue li {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		border-bottom: 1px solid var(--border);
		padding: 0.35rem 0.5rem;
	}

	.queue li.current {
		background: var(--surface);
		border-radius: var(--radius);
	}

	.grip {
		color: var(--muted);
		flex: 0 0 auto;
	}

	.track-main {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		flex: 1;
		min-width: 0;
		background: transparent;
		border: 0;
		color: inherit;
		font: inherit;
		text-align: left;
		padding: 0.25rem;
		cursor: pointer;
	}

	.track-main:hover .track-title {
		text-decoration: underline;
	}

	.track-main:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: 2px;
	}

	.thumb {
		flex: 0 0 auto;
		width: 2.25rem;
		height: 2.25rem;
		border-radius: calc(var(--radius) - 2px);
		overflow: hidden;
		display: flex;
		align-items: center;
		justify-content: center;
		background: var(--surface-2);
		color: var(--muted);
	}

	.thumb img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.track-title {
		font-weight: 500;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.queue li.current .track-title {
		color: var(--accent);
	}

	.track-artist,
	.track-duration {
		color: var(--muted);
		font-size: 0.85rem;
		white-space: nowrap;
	}

	.track-artist {
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.track-duration {
		margin-left: auto;
		font-variant-numeric: tabular-nums;
	}

	.row-btn {
		flex: 0 0 auto;
	}

	@media (max-width: 720px) {
		.hero {
			flex-direction: column;
			align-items: center;
			text-align: left;
		}

		.details {
			width: 100%;
		}

		.details h1 {
			white-space: normal;
		}

		.art {
			width: clamp(160px, 45vw, 260px);
		}

		.controls-row {
			justify-content: center;
		}

		.title-fav {
			display: inline-flex;
		}

		.controls-fav {
			display: none;
		}

		/* Phones use the device volume keys and the media session controls. */
		.volume-control {
			display: none;
		}
	}

	@media (max-width: 900px) {
		/* Tablets keep the side-by-side hero but still need compact queue rows. */
		.track-artist {
			display: none;
		}
	}
</style>
