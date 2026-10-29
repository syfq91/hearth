<script lang="ts">
	import MediaCard from './MediaCard.svelte';
	import type { S } from '../api/types';

	type CardItem = S['ItemSummary'] | S['NextUpEpisode'];

	interface Props {
		title: string;
		items: CardItem[];
		/** Heading level for the shelf title. */
		level?: 2;
	}

	let { title, items, level = 2 }: Props = $props();
</script>

<section class="shelf" aria-label={title}>
	<svelte:element this={`h${level}`} class="heading">{title}</svelte:element>
	{#if items.length === 0}
		<p class="empty">Nothing here yet.</p>
	{:else}
		<ul class="row">
			{#each items as entry (entry.id)}
				<li><MediaCard item={entry} /></li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	.shelf {
		margin-bottom: 2rem;
	}

	.heading {
		font-size: 1.1rem;
		margin-bottom: 0.75rem;
	}

	.row {
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: clamp(140px, 18vw, 200px);
		gap: 1rem;
		overflow-x: auto;
		padding: 0.25rem 0.25rem 0.75rem;
		margin: 0;
		list-style: none;
		scroll-snap-type: x proximity;
	}

	.row li {
		scroll-snap-align: start;
	}

	.empty {
		color: var(--muted);
	}
</style>
