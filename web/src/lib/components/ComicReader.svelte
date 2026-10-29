<script lang="ts">
	/**
	 * Comic reader (CBZ/CBR/PDF page mode).
	 *
	 * Consumes Hearth's on-demand page API (`/books/{id}/pages/{n}`) rather
	 * than shipping the whole archive to the browser, and keeps a 5-page
	 * window in memory with priority-ordered prefetch
	 * (docs/READER_INTEGRATION.md §4).
	 */
	import { withMediaToken } from '../media-token.svelte';
	import { untrack } from 'svelte';

	interface Props {
		bookId: string;
		pageCount: number;
		/** 1-indexed page to start on (from the stored locator `page:N`). */
		startPage?: number;
		onPageChange?: (page: number) => void;
	}

	let { bookId, pageCount, startPage = 1, onPageChange }: Props = $props();

	// Seeded once from the server locator; clamped into the valid range.
	let page = $state(untrack(() => Math.min(Math.max(startPage, 1), Math.max(pageCount, 1))));
	/** Resolved URLs for the pages currently in the window. */
	let cache = $state<Record<number, string>>({});
	let loading = $state(false);

	const WINDOW_BEFORE = 2;
	const WINDOW_AFTER = 2;

	async function urlFor(target: number): Promise<string> {
		return withMediaToken(`/api/v1/books/${bookId}/pages/${target}`, { width: 1600 });
	}

	/** Prefetch the window around `current`, nearest first. */
	async function prefetch(current: number): Promise<void> {
		const offsets = [1, -1, 2, -2];
		for (const offset of offsets) {
			const target = current + offset;
			if (target < 1 || target > pageCount || cache[target]) continue;
			const resolved = await urlFor(target);
			if (cache[target]) continue;
			cache = { ...cache, [target]: resolved };
		}
	}

	$effect(() => {
		// Prime the token and the window for the current page.
		void (async () => {
			loading = !cache[page];
			if (!cache[page]) {
				const resolved = await urlFor(page);
				cache = { ...cache, [page]: resolved };
			}
			loading = false;
			void prefetch(page);
			// Trim pages far outside the window so memory stays bounded.
			const keep = new Set<number>();
			for (let i = -WINDOW_BEFORE; i <= WINDOW_AFTER; i += 1) {
				const target = page + i;
				if (target >= 1 && target <= pageCount) keep.add(target);
			}
			const trimmed: Record<number, string> = {};
			for (const [key, value] of Object.entries(cache)) {
				if (keep.has(Number(key))) trimmed[Number(key)] = value;
			}
			cache = trimmed;
		})();
	});

	$effect(() => {
		onPageChange?.(page);
	});

	function go(delta: number): void {
		page = Math.min(Math.max(page + delta, 1), Math.max(pageCount, 1));
	}

	function handleKeydown(event: KeyboardEvent): void {
		const target = event.target as HTMLElement | null;
		if (target && (target.tagName === 'INPUT' || target.tagName === 'SELECT')) return;
		// RTL comics invert the arrow semantics.
		switch (event.key) {
			case 'ArrowRight':
				event.preventDefault();
				go(1);
				break;
			case 'ArrowLeft':
				event.preventDefault();
				go(-1);
				break;
			default:
				break;
		}
	}

</script>

<svelte:window onkeydown={handleKeydown} />

<div class="comic" role="document" aria-label={`Page ${page} of ${pageCount}`}>
	{#if cache[page]}
		<img
			class="page"
			src={cache[page]}
			alt={`Page ${page}`}
		decoding="async"
		/>
	{:else}
		<p class="status" role="status">Loading page…</p>
	{/if}

	<div class="controls">
		<button type="button" class="ghost" onclick={() => go(-1)} disabled={page <= 1}>
			← Previous
		</button>
		<span class="counter" aria-live="polite">{page} / {pageCount}</span>
		<button type="button" class="ghost" onclick={() => go(1)} disabled={page >= pageCount}>
			Next →
		</button>
	</div>

	{#if loading}
		<p class="visually-hidden" role="status">Fetching page</p>
	{/if}
</div>

<style>
	.comic {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 1rem;
	}

	.page {
		max-width: 100%;
		max-height: 74vh;
		object-fit: contain;
		background: #000;
		border-radius: var(--radius);
	}

	.controls {
		display: flex;
		align-items: center;
		gap: 1rem;
	}

	.counter {
		color: var(--muted);
		font-variant-numeric: tabular-nums;
		min-width: 6ch;
		text-align: center;
	}

	.ghost {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.45rem 1rem;
		cursor: pointer;
	}

	.ghost:hover:not(:disabled) {
		background: var(--surface);
	}

	.ghost:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.status {
		color: var(--muted);
		padding: 2rem;
	}
</style>
