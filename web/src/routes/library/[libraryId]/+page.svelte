<script lang="ts">
	import { api } from '../../../lib/api/client';
	import MediaCard from '../../../lib/components/MediaCard.svelte';
	import type { ItemPage } from '../../../lib/api/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	type SortKey = 'title' | 'added' | 'year' | 'rating';

	const SORTS: { value: SortKey; label: string }[] = [
		{ value: 'title', label: 'Title' },
		{ value: 'added', label: 'Date added' },
		{ value: 'year', label: 'Year' },
		{ value: 'rating', label: 'Rating' }
	];

	let sort = $state<SortKey>('title');
	let order = $state<'asc' | 'desc'>('asc');
	let genre = $state('');
	let year = $state('');
	let watched = $state<'' | 'true' | 'false'>('');

	let items = $state<ItemPage['items']>([]);
	let nextCursor = $state<string | null>(null);
	let loading = $state(false);
	let loadingMore = $state(false);
	let error = $state<string | null>(null);

	// Reset pagination when any filter changes, then reload the first page.
	let filterKey = $derived(`${sort}|${order}|${genre}|${year}|${watched}`);

	$effect(() => {
		void loadFirstPage(filterKey);
	});

	async function loadFirstPage(_key: string): Promise<void> {
		loading = true;
		error = null;
		try {
			const page = await api.get<ItemPage>(`/libraries/${data.library.id}/items`, {
				query: {
					sort,
					order,
					limit: 48,
					genre: genre || undefined,
					year: year ? Number(year) : undefined,
					watched: watched === '' ? undefined : watched === 'true'
				}
			});
			items = page.items;
			nextCursor = page.next_cursor ?? null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			items = [];
			nextCursor = null;
		} finally {
			loading = false;
		}
	}

	async function loadMore(): Promise<void> {
		if (!nextCursor || loadingMore) return;
		loadingMore = true;
		try {
			const page = await api.get<ItemPage>(`/libraries/${data.library.id}/items`, {
				query: {
					sort,
					order,
					limit: 48,
					cursor: nextCursor,
					genre: genre || undefined,
					year: year ? Number(year) : undefined,
					watched: watched === '' ? undefined : watched === 'true'
				}
			});
			items = [...items, ...page.items];
			nextCursor = page.next_cursor ?? null;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loadingMore = false;
		}
	}

	// Genre suggestions come from the cards already on screen, so the filter
	// works before any dedicated genres endpoint exists.
	let genreSuggestions = $derived(
		[...new Set(items.flatMap((item) => item.genres))].sort((a, b) => a.localeCompare(b))
	);
</script>

<nav class="crumbs" aria-label="Breadcrumb">
	<a href="/library">Libraries</a>
	<span aria-hidden="true">/</span>
	<span>{data.library.name}</span>
</nav>

<h1>{data.library.name}</h1>

<form class="filters" aria-label="Filter items">
	<label>
		<span>Sort by</span>
		<select bind:value={sort}>
			{#each SORTS as option (option.value)}
				<option value={option.value}>{option.label}</option>
			{/each}
		</select>
	</label>
	<label>
		<span>Order</span>
		<select bind:value={order}>
			<option value="asc">Ascending</option>
			<option value="desc">Descending</option>
		</select>
	</label>
	<label>
		<span>Genre</span>
		<input
			type="search"
			list="genre-options"
			bind:value={genre}
			placeholder="Any"
			aria-describedby="genre-hint"
		/>
		<datalist id="genre-options">
			{#each genreSuggestions as option (option)}
				<option value={option}></option>
			{/each}
		</datalist>
		<span id="genre-hint" class="visually-hidden">
			Type a genre; suggestions appear from the loaded items.
		</span>
	</label>
	<label>
		<span>Year</span>
		<input type="number" min="1800" max="2100" bind:value={year} placeholder="Any" />
	</label>
	<label>
		<span>Watched</span>
		<select bind:value={watched}>
			<option value="">Any</option>
			<option value="false">Unwatched</option>
			<option value="true">Watched</option>
		</select>
	</label>
</form>

{#if error}
	<p class="banner" role="alert">{error}</p>
{/if}

{#if loading}
	<p class="muted" role="status">Loading…</p>
{:else if items.length === 0 && !error}
	<p class="muted">No items match these filters.</p>
{:else}
	<ul class="grid">
		{#each items as item (item.id)}
			<li><MediaCard item={item} /></li>
		{/each}
	</ul>
	{#if nextCursor}
		<div class="more">
			<button type="button" class="load-more" onclick={loadMore} disabled={loadingMore}>
				{loadingMore ? 'Loading…' : 'Load more'}
			</button>
		</div>
	{/if}
{/if}

<style>
	.crumbs {
		display: flex;
		gap: 0.5rem;
		color: var(--muted);
		font-size: 0.9rem;
		margin-bottom: 0.5rem;
	}

	.filters {
		display: flex;
		flex-wrap: wrap;
		gap: 1rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.9rem 1rem;
		margin-bottom: 1.25rem;
	}

	.filters label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.85rem;
		color: var(--muted);
	}

	.filters select,
	.filters input {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.35rem 0.5rem;
		min-width: 8rem;
	}

	.banner {
		background: var(--surface);
		border: 1px solid var(--border);
		border-left: 3px solid var(--danger);
		border-radius: var(--radius);
		padding: 0.6rem 1rem;
		color: var(--muted);
	}

	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 1.25rem 1rem;
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.more {
		display: flex;
		justify-content: center;
		padding: 1.5rem 0;
	}

	.load-more {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.6rem 1.4rem;
		cursor: pointer;
	}

	.load-more:hover:not(:disabled) {
		background: var(--surface);
	}

	.load-more:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.muted {
		color: var(--muted);
	}
</style>
