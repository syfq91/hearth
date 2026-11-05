<script lang="ts">
	import MusicTabs from '../../../lib/components/MusicTabs.svelte';
	import { api } from '../../../lib/api/client';
	import type { TrackListResponse, TrackSummary } from '../../../lib/api/types';
	import { playQueue, player, type PlayerTrack } from '../../../lib/player.svelte';
	import { goto } from '$app/navigation';
	import { untrack } from 'svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let search = $state(untrack(() => data.query));
	let items = $state(untrack(() => data.tracks.items));
	let total = $state(untrack(() => data.tracks.total));
	let loadingMore = $state(false);
	let error = $state<string | null>(null);

	// The first page arrives from the loader; every later page appends here.
	$effect(() => {
		items = data.tracks.items;
		total = data.tracks.total;
	});

	function submitSearch(event: SubmitEvent): void {
		event.preventDefault();
		const trimmed = search.trim();
		void goto(trimmed ? `/music/songs?q=${encodeURIComponent(trimmed)}` : '/music/songs');
	}

	function toPlayerTrack(track: TrackSummary): PlayerTrack {
		return {
			id: track.id,
			title: track.title,
			artistName: track.artist_name ?? null,
			albumTitle: track.album_title ?? null,
			streamUrl: track.stream_url
		};
	}

	function play(track: TrackSummary): void {
		const queue = items.map(toPlayerTrack);
		const index = queue.findIndex((item) => item.id === track.id);
		void playQueue(queue, Math.max(0, index));
	}

	async function loadMore(): Promise<void> {
		if (loadingMore || items.length >= total) return;
		loadingMore = true;
		error = null;
		try {
			const page = await api.get<TrackListResponse>('/tracks', {
				query: { query: search.trim() || undefined, limit: 48, offset: items.length }
			});
			items = [...items, ...page.items];
			total = page.total;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loadingMore = false;
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
	<title>Songs · Hearth</title>
</svelte:head>

<h1>Songs</h1>

<MusicTabs current="songs" />

<form class="search" role="search" onsubmit={submitSearch}>
	<label class="visually-hidden" for="song-search">Search songs</label>
	<input id="song-search" type="search" bind:value={search} placeholder="Search songs…" />
	<button type="submit">Search</button>
</form>

{#if data.query}
	<p class="muted">
		Results for “{data.query}” —
		<a href="/music/songs">clear</a>
	</p>
{/if}

{#if error}
	<p class="error" role="alert">{error}</p>
{/if}

{#if items.length === 0}
	<p class="muted">No songs found.</p>
{:else}
	<ol class="songs">
		{#each items as track (track.id)}
			<li class="song" class:current={player.track?.id === track.id}>
				<span class="number">{track.track_number ?? ''}</span>
				<button type="button" class="name" onclick={() => play(track)}>{track.title}</button>
				<span class="artist">
					{#if track.artist_id}
						<a href={`/music/artist/${track.artist_id}`}>{track.artist_name}</a>
					{:else}
						{track.artist_name ?? ''}
					{/if}
				</span>
				<span class="album">
					{#if track.album_id}
						<a href={`/music/album/${track.album_id}`}>{track.album_title}</a>
					{:else}
						{track.album_title ?? ''}
					{/if}
				</span>
				<span class="duration">{formatDuration(track.duration_seconds)}</span>
			</li>
		{/each}
	</ol>

	{#if items.length < total}
		<button type="button" class="load-more" onclick={loadMore} disabled={loadingMore}>
			{loadingMore ? 'Loading…' : 'Load more'}
		</button>
	{/if}
{/if}

<style>
	.search {
		display: flex;
		gap: 0.5rem;
		margin-bottom: 1.25rem;
		max-width: 28rem;
	}

	.search input {
		flex: 1;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.5rem 0.75rem;
	}

	.search button {
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		border: 0;
		border-radius: var(--radius);
		padding: 0.5rem 1.1rem;
		cursor: pointer;
	}

	.songs {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 0.15rem;
	}

	.song {
		display: grid;
		grid-template-columns: 2.5rem minmax(8rem, 2fr) minmax(0, 1.5fr) minmax(0, 1.5fr) auto;
		align-items: center;
		gap: 0.75rem;
		padding: 0.4rem 0.6rem;
		border-radius: 6px;
	}

	.song:hover {
		background: var(--surface);
	}

	.song.current .name {
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

	.artist,
	.album {
		color: var(--muted);
		font-size: 0.9rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.duration {
		color: var(--muted);
		font-size: 0.85rem;
		font-variant-numeric: tabular-nums;
	}

	.load-more {
		display: block;
		margin: 1.5rem auto 0;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.5rem 1.4rem;
		cursor: pointer;
		color: inherit;
	}

	.muted {
		color: var(--muted);
	}

	.error {
		color: var(--danger);
	}

	@media (max-width: 720px) {
		.song {
			grid-template-columns: 2rem 1fr auto;
		}

		.artist,
		.album {
			display: none;
		}
	}
</style>
