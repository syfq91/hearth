<script lang="ts">
	import MusicTabs from '../../../lib/components/MusicTabs.svelte';
	import Pagination from '../../../lib/components/Pagination.svelte';
	import { api } from '../../../lib/api/client';
	import type { GenreListResponse } from '../../../lib/api/types';
	import { goto } from '$app/navigation';
	import { untrack } from 'svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const PAGE_SIZE = 48;

	let search = $state(untrack(() => data.query));
	let items = $state(untrack(() => data.genres.items));
	let total = $state(untrack(() => data.genres.total));
	let page = $state(1);
	let loading = $state(false);
	let error = $state<string | null>(null);

	let totalPages = $derived(Math.max(1, Math.ceil(total / PAGE_SIZE)));

	// The first page arrives from the loader. A new search replaces it and
	// starts again at page 1.
	$effect(() => {
		items = data.genres.items;
		total = data.genres.total;
		page = 1;
	});

	function submitSearch(event: SubmitEvent): void {
		event.preventDefault();
		const trimmed = search.trim();
		void goto(trimmed ? `/music/genres?q=${encodeURIComponent(trimmed)}` : '/music/genres');
	}

	function goToPage(next: number): void {
		const target = Math.min(Math.max(next, 1), totalPages);
		if (target === page) return;
		page = target;
		void loadPage();
	}

	async function loadPage(): Promise<void> {
		loading = true;
		error = null;
		try {
			const result = await api.get<GenreListResponse>('/genres', {
				query: {
					query: search.trim() || undefined,
					limit: PAGE_SIZE,
					offset: (page - 1) * PAGE_SIZE
				}
			});
			items = result.items;
			total = result.total;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}
</script>

<svelte:head>
	<title>Genres · Hearth</title>
</svelte:head>

<h1>Genres</h1>

<MusicTabs current="genres" />

<form class="search" role="search" onsubmit={submitSearch}>
	<label class="visually-hidden" for="genre-search">Search genres</label>
	<input id="genre-search" type="search" bind:value={search} placeholder="Search genres…" />
	<button type="submit">Search</button>
</form>

{#if data.query}
	<p class="muted">
		Results for “{data.query}” —
		<a href="/music/genres">clear</a>
	</p>
{/if}

{#if error}
	<p class="error" role="alert">{error}</p>
{/if}

{#if items.length === 0}
	<p class="muted">No genres found.</p>
{:else}
	<ul class="grid">
		{#each items as genre (genre.name)}
			<li>
				<article class="genre">
					<h2 class="title">
						<a href={`/music?genre=${encodeURIComponent(genre.name)}`}>{genre.name}</a>
					</h2>
					<p class="counts">
						{[
							genre.album_count ? `${genre.album_count} albums` : null,
							genre.track_count ? `${genre.track_count} tracks` : null
						]
							.filter(Boolean)
							.join(' · ')}
					</p>
				</article>
			</li>
		{/each}
	</ul>

	<Pagination
		{page}
		{totalPages}
		{total}
		onPage={goToPage}
		disabled={loading}
		itemLabel="genre"
	/>
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
		grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
		gap: 1rem;
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.genre {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.9rem 1rem;
	}

	.title {
		font-size: 1rem;
		font-weight: 600;
		margin: 0 0 0.3rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.counts {
		color: var(--muted);
		font-size: 0.85rem;
		margin: 0;
	}

	.muted {
		color: var(--muted);
	}

	.error {
		color: var(--danger);
	}
</style>
