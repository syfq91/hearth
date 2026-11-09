<script lang="ts">
	import MediaCard from './MediaCard.svelte';
	import type { S } from '../api/types';

	type CardItem = S['ItemSummary'] | S['NextUpEpisode'];

	interface Props {
		title: string;
		items: CardItem[];
		/** Heading level for the shelf title. */
		level?: 2;
		/** When true, video cards navigate directly to playback. */
		directPlay?: boolean;
		/** Optional destination link for the shelf heading. */
		href?: string;
	}

	let { title, items, level = 2, directPlay = false, href }: Props = $props();
</script>

<section class="shelf" aria-label={title}>
	<svelte:element this={`h${level}`} class="heading">
		{#if href}
			<a class="heading-link" {href}>{title}</a>
		{:else}
			{title}
		{/if}
	</svelte:element>
	{#if items.length === 0}
		<p class="empty">Nothing here yet.</p>
	{:else}
		<ul class="row">
			{#each items as entry (entry.id)}
				<li><MediaCard item={entry} {directPlay} /></li>
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

	.heading-link {
		color: inherit;
		text-decoration: none;
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}

	.heading-link:hover {
		color: var(--accent);
		text-decoration: underline;
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
