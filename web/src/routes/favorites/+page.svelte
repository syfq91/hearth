<script lang="ts">
	import MediaCard from '../../lib/components/MediaCard.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head>
	<title>Favourites · Hearth</title>
</svelte:head>

<h1>Favourites</h1>

{#await data.favorites}
	<p class="muted" role="status">Loading favourites…</p>
{:then favorites}
	{#if favorites.items.length === 0}
		<p class="muted">No favourites yet. Mark items as favourites from their detail page.</p>
	{:else}
		<ul class="grid">
			{#each favorites.items as item (item.id)}
				<li><MediaCard item={item} /></li>
			{/each}
		</ul>
	{/if}
{:catch error}
	<p class="banner" role="alert">{error instanceof Error ? error.message : String(error)}</p>
{/await}

<style>
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 1.25rem 1rem;
		list-style: none;
		padding: 0;
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
