<script lang="ts">
	import Poster from '../../../../lib/components/Poster.svelte';
	import { api } from '../../../../lib/api/client';
	import {
		addToQueue,
		playQueue,
		player,
		queueNext,
		toggleShuffle,
		type PlayerTrack
	} from '../../../../lib/player.svelte';
	import type { TrackSummary } from '../../../../lib/api/types';
	import type { PageProps } from './$types';
	import { CornerDownRight, Ellipsis, Heart, ListPlus, Shuffle } from '@lucide/svelte';

	let { data }: { data: Awaited<PageProps['data']['detail']> } = $props();

	// Local override so the heart reacts instantly; the next navigation refetches
	// the authoritative watch state.
	let favoriteOverride = $state<boolean | null>(null);
	let favoritePending = $state(false);
	const favorite = $derived(favoriteOverride ?? data.album.watch_state?.favorite ?? false);

	// Track id whose options menu is open; only one row at a time.
	let openMenu = $state<string | null>(null);

	$effect(() => {
		if (!openMenu) return;
		function onOutsideClick(event: MouseEvent): void {
			const target = event.target;
			if (target instanceof Element && target.closest('[data-track-menu]')) return;
			openMenu = null;
		}
		function onKeydown(event: KeyboardEvent): void {
			if (event.key === 'Escape') openMenu = null;
		}
		window.addEventListener('click', onOutsideClick);
		window.addEventListener('keydown', onKeydown);
		return () => {
			window.removeEventListener('click', onOutsideClick);
			window.removeEventListener('keydown', onKeydown);
		};
	});

	function toPlayerTrack(track: TrackSummary): PlayerTrack {
		return {
			id: track.id,
			title: track.title,
			artistName: track.artist_name ?? null,
			albumTitle: track.album_title ?? null,
			streamUrl: track.stream_url,
			artworkUrl: data.album.poster?.url ?? null,
			albumId: track.album_id ?? data.album.id,
			favorite: track.watch_state?.favorite ?? false,
			durationSeconds: track.duration_seconds,
			replayGainTrackGain: track.replaygain_track_gain,
			replayGainTrackPeak: track.replaygain_track_peak,
			replayGainAlbumGain: track.replaygain_album_gain,
			replayGainAlbumPeak: track.replaygain_album_peak
		};
	}

	/** The whole album in disc/track order, as one queue. */
	function albumQueue(): PlayerTrack[] {
		return data.album.discs.flatMap((disc) => disc.tracks.map(toPlayerTrack));
	}

	async function play(track: TrackSummary): Promise<void> {
		const queue = albumQueue();
		const index = queue.findIndex((item) => item.id === track.id);
		await playQueue(queue, Math.max(0, index));
	}

	function playAll(): void {
		const queue = albumQueue();
		if (queue.length > 0) void playQueue(queue, 0);
	}

	/** Start the album with shuffle enabled (the player's global shuffle mode). */
	function shuffleAll(): void {
		const queue = albumQueue();
		if (queue.length === 0) return;
		if (!player.shuffle) toggleShuffle();
		void playQueue(queue, 0);
	}

	function addTrackToQueue(track: TrackSummary): void {
		addToQueue(toPlayerTrack(track));
		openMenu = null;
	}

	function queueTrackNext(track: TrackSummary): void {
		queueNext(toPlayerTrack(track));
		openMenu = null;
	}

	async function toggleFavorite(): Promise<void> {
		if (favoritePending) return;
		const previous = favorite;
		const next = !previous;
		favoriteOverride = next;
		favoritePending = true;
		try {
			await api.post(`/items/${data.album.id}/favorite`, { body: { favorite: next } });
		} catch {
			favoriteOverride = previous;
		} finally {
			favoritePending = false;
		}
	}

	function formatDuration(seconds: number | null | undefined): string {
		if (!seconds || seconds <= 0) return '';
		const m = Math.floor(seconds / 60);
		const s = Math.floor(seconds % 60);
		return `${m}:${s.toString().padStart(2, '0')}`;
	}
</script>

<svelte:head>
	<title>{data.album.title} · Hearth</title>
</svelte:head>

<article class="album">
	<div class="art">
		<Poster image={data.album.poster} alt="" aspect="1 / 1" width={400} />
	</div>

	<div class="info">
		<nav class="crumbs" aria-label="Breadcrumb">
			<a href="/music">Music</a>
		</nav>
		<h1>{data.album.title}</h1>
		{#if data.album.artist_name}
			<p class="artist">
				{#if data.album.artist_id}
					<a href={`/music/artist/${data.album.artist_id}`}>{data.album.artist_name}</a>
				{:else}
					{data.album.artist_name}
				{/if}
			</p>
		{/if}
		<p class="meta">
			{[
				data.album.year,
				data.album.total_tracks ? `${data.album.total_tracks} tracks` : null,
				formatDuration(data.album.duration_seconds)
			]
				.filter(Boolean)
				.join(' · ')}
		</p>
		<div class="actions">
			<button type="button" class="play" onclick={playAll}>Play album</button>
			<button
				type="button"
				class="ghost icon"
				onclick={shuffleAll}
				aria-label="Shuffle album"
				title="Shuffle album"
			>
				<Shuffle size={18} />
			</button>
			<button
				type="button"
				class="ghost icon"
				class:active={favorite}
				onclick={() => void toggleFavorite()}
				disabled={favoritePending}
				aria-label={favorite ? 'Remove album from favorites' : 'Add album to favorites'}
				aria-pressed={favorite}
				title={favorite ? 'Remove from favorites' : 'Favourite'}
			>
				<Heart size={18} fill={favorite ? 'currentColor' : 'none'} />
			</button>
		</div>
	</div>
</article>

{#each data.album.discs as disc (disc.disc_number)}
	<section class="disc" aria-label={`Disc ${disc.disc_number}`}>
		{#if (data.album.total_discs ?? 1) > 1}
			<h2>Disc {disc.disc_number}</h2>
		{/if}
		<ol class="tracks">
			{#each disc.tracks as track (track.id)}
				<li class="track" class:current={player.track?.id === track.id}>
					<span class="number">{track.track_number}</span>
					<button type="button" class="name" onclick={() => play(track)}>
						{track.title}
					</button>
					<span class="duration">{formatDuration(track.duration_seconds)}</span>
					<div class="track-actions" data-track-menu>
						<button
							type="button"
							class="more"
							onclick={() => (openMenu = openMenu === track.id ? null : track.id)}
							aria-label={`More options for ${track.title}`}
							aria-haspopup="menu"
							aria-expanded={openMenu === track.id}
							title="Track options"
						>
							<Ellipsis size={18} />
						</button>
						{#if openMenu === track.id}
							<div class="track-menu" role="menu" aria-label={`Options for ${track.title}`}>
								<button type="button" role="menuitem" onclick={() => addTrackToQueue(track)}>
									<ListPlus size={16} />
									<span>Add to queue</span>
								</button>
								<button type="button" role="menuitem" onclick={() => queueTrackNext(track)}>
									<CornerDownRight size={16} />
									<span>Play next</span>
								</button>
							</div>
						{/if}
					</div>
				</li>
			{/each}
		</ol>
	</section>
{/each}

<style>
	.album {
		display: grid;
		grid-template-columns: minmax(180px, 260px) 1fr;
		gap: 2rem;
		align-items: start;
		margin-bottom: 2rem;
	}

	.art {
		border-radius: var(--radius);
		overflow: hidden;
	}

	.crumbs {
		font-size: 0.9rem;
		color: var(--muted);
		margin-bottom: 0.5rem;
	}

	.info h1 {
		font-size: 1.7rem;
		margin-bottom: 0.25rem;
	}

	.artist {
		color: var(--muted);
		margin: 0 0 0.5rem;
	}

	.meta {
		color: var(--muted);
		font-size: 0.9rem;
		margin: 0 0 1rem;
	}

	.play {
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		border: 0;
		border-radius: var(--radius);
		padding: 0.6rem 1.5rem;
		cursor: pointer;
	}

	.play:hover {
		filter: brightness(1.05);
	}

	.actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.ghost.icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 2.5rem;
		height: 2.55rem;
		padding: 0.6rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		color: inherit;
		cursor: pointer;
	}

	.ghost.icon:hover:not(:disabled) {
		background: var(--surface);
		color: var(--accent);
		border-color: var(--accent);
	}

	.ghost.icon:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.ghost.icon.active {
		color: var(--accent);
		border-color: var(--accent);
	}

	.disc h2 {
		font-size: 1rem;
		color: var(--muted);
		margin-bottom: 0.5rem;
	}

	.tracks {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 0.15rem;
	}

	.track {
		display: grid;
		grid-template-columns: 2.5rem 1fr auto auto;
		align-items: center;
		gap: 0.75rem;
		padding: 0.4rem 0.6rem;
		border-radius: 6px;
	}

	.track:hover {
		background: var(--surface);
	}

	.track.current .name {
		color: var(--accent);
	}

	.number {
		color: var(--muted);
		text-align: right;
		font-variant-numeric: tabular-nums;
	}

	.name {
		background: none;
		border: 0;
		color: inherit;
		text-align: left;
		padding: 0;
		cursor: pointer;
		font: inherit;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.name:hover {
		text-decoration: underline;
	}

	.duration {
		color: var(--muted);
		font-size: 0.85rem;
		font-variant-numeric: tabular-nums;
	}

	.track-actions {
		position: relative;
		display: inline-flex;
	}

	.more {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2rem;
		height: 2rem;
		background: transparent;
		border: 1px solid transparent;
		border-radius: var(--radius);
		color: var(--muted);
		cursor: pointer;
	}

	.more:hover,
	.more[aria-expanded='true'] {
		background: var(--surface-2);
		color: var(--accent);
	}

	.more:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: 2px;
	}

	.track-menu {
		position: absolute;
		top: calc(100% + 0.25rem);
		right: 0;
		z-index: 30;
		min-width: 10rem;
		padding: 0.35rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: 0 6px 22px rgba(0, 0, 0, 0.3);
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
	}

	.track-menu button {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		width: 100%;
		padding: 0.45rem 0.6rem;
		background: transparent;
		border: 0;
		border-radius: calc(var(--radius) - 2px);
		color: var(--text);
		font: inherit;
		font-size: 0.85rem;
		text-align: left;
		cursor: pointer;
	}

	.track-menu button:hover {
		background: var(--surface-2);
		color: var(--accent);
	}

	.track-menu button:focus-visible {
		outline: 2px solid var(--focus-ring);
		outline-offset: 1px;
	}

	@media (max-width: 720px) {
		.album {
			grid-template-columns: 1fr;
		}

		.art {
			max-width: 200px;
		}
	}
</style>
