<script lang="ts">
	import MusicTabs from '../../lib/components/MusicTabs.svelte';
	import Poster from '../../lib/components/Poster.svelte';
	import type { AlbumListResponse } from '../../lib/api/types';
	import { goto } from '$app/navigation';
	import { untrack } from 'svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// Seeded once from the URL; the box keeps whatever the user types even if
	// the query changes underneath it.
	let search = $state(untrack(() => data.query));
	let albums = $state<AlbumListResponse | null>(null);
	let error = $state<string | null>(null);

	// The loader returns a promise so navigation is not held up by the request.
	$effect(() => {
		let cancelled = false;
		albums = null;
		error = null;
		void data.albums
			.then((value) => {
				if (!cancelled) albums = value;
			})
			.catch((e) => {
				if (!cancelled) error = e instanceof Error ? e.message : String(e);
			});
		return () => {
			cancelled = true;
		};
	});

	function submitSearch(event: SubmitEvent): void {
		event.preventDefault();
		const trimmed = search.trim();
		const params = new URLSearchParams();
		if (trimmed) params.set('q', trimmed);
		if (data.genre) params.set('genre', data.genre);
		void goto(params.size > 0 ? `/music?${params.toString()}` : '/music');
	}
</script>

<svelte:head>
	<title>Music · Hearth</title>
</svelte:head>

<h1>Music</h1>

<MusicTabs current="albums" />

<form class="search" role="search" onsubmit={submitSearch}>
	<label class="visually-hidden" for="music-search">Search albums</label>
	<input
		id="music-search"
		type="search"
		bind:value={search}
		placeholder="Search albums…"
	/>
	<button type="submit">Search</button>
</form>

{#if data.query}
	<p class="muted">
		Results for “{data.query}” —
		<a href={data.genre ? `/music?genre=${encodeURIComponent(data.genre)}` : '/music'}>clear</a>
	</p>
{/if}

{#if data.genre}
	<p class="muted">
		Genre “{data.genre}” —
		<a href={data.query ? `/music?q=${encodeURIComponent(data.query)}` : '/music'}>clear</a>
	</p>
{/if}

{#if error}
	<p class="banner" role="alert">{error}</p>
{:else if albums === null}
	<p class="muted" role="status">Loading albums…</p>
{:else if albums.items.length === 0}
	<p class="muted">No albums found.</p>
{:else}
	<ul class="grid">
		{#each albums.items as album (album.id)}
			<li>
				<article class="album">
					<a href={`/music/album/${album.id}`} aria-label={album.title}>
						<Poster image={album.poster} alt="" aspect="1 / 1" width={300} />
					</a>
					<h2 class="title">
						<a href={`/music/album/${album.id}`}>{album.title}</a>
					</h2>
					{#if album.artist_name}
						<p class="artist">{album.artist_name}</p>
					{/if}
					{#if album.track_count}
						<p class="count">{album.track_count} tracks</p>
					{/if}
				</article>
			</li>
		{/each}
	</ul>
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

	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
		gap: 1.25rem 1rem;
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.album {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}

	.title {
		font-size: 0.95rem;
		font-weight: 600;
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.artist {
		color: var(--muted);
		font-size: 0.85rem;
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.count {
		color: var(--muted);
		font-size: 0.75rem;
		margin: 0;
	}

	.banner {
		background: var(--surface);
		border: 1px solid var(--border);
		border-left: 3px solid var(--danger);
		border-radius: var(--radius);
		padding: 0.6rem 1rem;
		color: var(--muted);
	}

	.muted {
		color: var(--muted);
	}
</style>
