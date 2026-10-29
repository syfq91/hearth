<script lang="ts">
	import MediaCard from '../../lib/components/MediaCard.svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { untrack } from 'svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const TYPES = [
		{ value: 'all', label: 'All' },
		{ value: 'movie', label: 'Movies' },
		{ value: 'series', label: 'Shows' },
		{ value: 'episode', label: 'Episodes' },
		{ value: 'album', label: 'Albums' },
		{ value: 'track', label: 'Tracks' },
		{ value: 'book', label: 'Books' },
		{ value: 'person', label: 'People' }
	] as const;

	let query = $state(untrack(() => data.q));
	let type = $state(untrack(() => data.type));

	// Debounced live search that keeps the URL in sync (shareable + back
	// button both work because SvelteKit replaces state rather than reloading).
	let debounce: ReturnType<typeof setTimeout> | null = null;
	$effect(() => {
		const q = query.trim();
		const t = type;
		if (debounce) clearTimeout(debounce);
		if (!q) {
			// Clear results immediately when the box empties.
			if (page.url.searchParams.get('q')) void goto('/search', { replace: true, reset: false });
			return;
		}
		debounce = setTimeout(() => {
			const next = `/search?q=${encodeURIComponent(q)}${t !== 'all' ? `&type=${t}` : ''}`;
			if (page.url.pathname + page.url.search !== next) {
				void goto(next, { replace: true, reset: false });
			}
		}, 300);
	});

	const results = $derived(data.results);

	const groups = $derived.by(() => {
		if (!results) return [];
		const built: { label: string; items: typeof results.movies }[] = [];
		if (results.movies.length) built.push({ label: 'Movies', items: results.movies });
		if (results.series.length) built.push({ label: 'Shows', items: results.series });
		if (results.episodes.length) built.push({ label: 'Episodes', items: results.episodes });
		if (results.albums.length) built.push({ label: 'Albums', items: results.albums });
		if (results.tracks.length) built.push({ label: 'Tracks', items: results.tracks });
		if (results.books.length) built.push({ label: 'Books', items: results.books });
		return built;
	});
</script>

<h1>Search</h1>

<form
	class="search"
	role="search"
	onsubmit={(event) => {
		event.preventDefault();
	}}
>
	<label class="visually-hidden" for="search-input">Search the library</label>
	<input
		id="search-input"
		bind:value={query}
		type="search"
		placeholder="Search movies, shows, music, books…"
		autocomplete="off"
	/>

	<label class="visually-hidden" for="search-type">Filter by type</label>
	<select id="search-type" bind:value={type}>
		{#each TYPES as option (option.value)}
			<option value={option.value}>{option.label}</option>
		{/each}
	</select>
</form>

{#if !data.q.trim()}
	<p class="muted">Type to search your libraries.</p>
{:else if results && results.total === 0}
	<p class="muted">No results for “{data.q}”.</p>
{:else if results}
	<p class="count" role="status">{results.total} results</p>

	{#each groups as group (group.label)}
		{#if group.items.length > 0}
			<section class="group" aria-label={group.label}>
				<h2>{group.label}</h2>
				<ul class="grid">
					{#each group.items as item (item.id)}
						<li><MediaCard item={item} /></li>
					{/each}
				</ul>
			</section>
		{/if}
	{/each}

	{#if results.people.length > 0}
		<section class="group" aria-label="People">
			<h2>People</h2>
			<ul class="people">
				{#each results.people as person (person.id)}
					<li>{person.name}</li>
				{/each}
			</ul>
		</section>
	{/if}
{/if}

<style>
	.search {
		display: flex;
		gap: 0.5rem;
		margin-bottom: 1.25rem;
		max-width: 40rem;
	}

	.search input {
		flex: 1;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.55rem 0.75rem;
	}

	.search select {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.55rem 0.6rem;
	}

	.count {
		color: var(--muted);
		font-size: 0.9rem;
		margin-bottom: 1rem;
	}

	.group {
		margin-bottom: 2rem;
	}

	.group h2 {
		font-size: 1.1rem;
		margin-bottom: 0.75rem;
	}

	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 1.25rem 1rem;
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.people {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem 1.25rem;
		list-style: none;
		padding: 0;
		margin: 0;
		color: var(--muted);
	}

	.muted {
		color: var(--muted);
	}
</style>
