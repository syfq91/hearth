<script lang="ts">
	import MediaCard from './MediaCard.svelte';
	import type { S } from '../api/types';
	import { ChevronLeft, ChevronRight } from '@lucide/svelte';

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

	let rowEl = $state<HTMLElement | null>(null);
	let canScrollLeft = $state(false);
	let canScrollRight = $state(false);
	let hasOverflow = $state(false);

	function updateScrollState() {
		if (!rowEl) return;
		const { scrollLeft, scrollWidth, clientWidth } = rowEl;
		hasOverflow = scrollWidth > clientWidth + 4;
		canScrollLeft = scrollLeft > 4;
		canScrollRight = scrollLeft + clientWidth < scrollWidth - 4;
	}

	function scrollRow(direction: 'left' | 'right') {
		if (!rowEl) return;
		const distance = rowEl.clientWidth * 0.75;
		rowEl.scrollBy({
			left: direction === 'left' ? -distance : distance,
			behavior: 'smooth'
		});
	}

	$effect(() => {
		if (!rowEl) return;
		const el = rowEl;
		updateScrollState();

		el.addEventListener('scroll', updateScrollState, { passive: true });
		const ro = new ResizeObserver(() => {
			updateScrollState();
		});
		ro.observe(el);

		return () => {
			el.removeEventListener('scroll', updateScrollState);
			ro.disconnect();
		};
	});
</script>

<section class="shelf" aria-label={title}>
	<div class="shelf-header">
		<svelte:element this={`h${level}`} class="heading">
			{#if href}
				<a class="heading-link" {href}>{title}</a>
			{:else}
				{title}
			{/if}
		</svelte:element>
		{#if hasOverflow}
			<div class="scroll-arrows">
				<button
					type="button"
					class="scroll-btn"
					onclick={() => scrollRow('left')}
					disabled={!canScrollLeft}
					aria-label={`Scroll ${title} left`}
					title="Scroll left"
				>
					<ChevronLeft size={18} />
				</button>
				<button
					type="button"
					class="scroll-btn"
					onclick={() => scrollRow('right')}
					disabled={!canScrollRight}
					aria-label={`Scroll ${title} right`}
					title="Scroll right"
				>
					<ChevronRight size={18} />
				</button>
			</div>
		{/if}
	</div>
	{#if items.length === 0}
		<p class="empty">Nothing here yet.</p>
	{:else}
		<ul class="row" bind:this={rowEl}>
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

	.shelf-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 0.75rem;
		gap: 0.5rem;
	}

	.heading {
		font-size: 1.1rem;
		margin: 0;
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

	.scroll-arrows {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}

	.scroll-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.85rem;
		height: 1.85rem;
		padding: 0;
		border-radius: var(--radius);
		background: var(--surface-2);
		border: 1px solid var(--border);
		color: var(--text);
		cursor: pointer;
		transition:
			background 0.15s ease,
			opacity 0.15s ease,
			color 0.15s ease,
			border-color 0.15s ease;
	}

	.scroll-btn:hover:not(:disabled) {
		background: var(--accent);
		color: var(--accent-contrast);
		border-color: var(--accent);
	}

	.scroll-btn:disabled {
		opacity: 0.3;
		cursor: default;
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
