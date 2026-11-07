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
	let error = $state<string | null>(null);
	let comic = $state<HTMLElement | null>(null);

	const WINDOW_BEFORE = 2;
	const WINDOW_AFTER = 2;

	async function urlFor(target: number): Promise<string> {
		return withMediaToken(`/api/v1/books/${bookId}/pages/${target}`, { width: 1600 });
	}

	$effect(() => {
		// Only depend on `page`, `bookId`, and `pageCount`
		const current = page;
		const total = pageCount;
		let cancelled = false;

		void (async () => {
			error = null;
			const initialCache = untrack(() => cache);
			if (!initialCache[current]) {
				loading = true;
				try {
					const resolved = await urlFor(current);
					if (cancelled) return;
					cache = { ...untrack(() => cache), [current]: resolved };
				} catch (e) {
					if (cancelled) return;
					error = e instanceof Error ? e.message : String(e);
				} finally {
					if (!cancelled) loading = false;
				}
			}

			// Prefetch the window around `current`, nearest first.
			const offsets = [1, -1, 2, -2];
			for (const offset of offsets) {
				const target = current + offset;
				if (target < 1 || target > total) continue;
				if (untrack(() => cache)[target]) continue;
				try {
					const resolved = await urlFor(target);
					if (cancelled) return;
					if (untrack(() => cache)[target]) continue;
					cache = { ...untrack(() => cache), [target]: resolved };
				} catch {
					// Prefetch is best-effort
				}
			}

			// Trim pages far outside the window so memory stays bounded.
			const keep = new Set<number>();
			for (let i = -WINDOW_BEFORE; i <= WINDOW_AFTER; i += 1) {
				const target = current + i;
				if (target >= 1 && target <= total) keep.add(target);
			}
			const latestCache = untrack(() => cache);
			let needsTrim = false;
			const trimmed: Record<number, string> = {};
			for (const [key, value] of Object.entries(latestCache)) {
				const numKey = Number(key);
				if (keep.has(numKey)) {
					trimmed[numKey] = value;
				} else {
					needsTrim = true;
				}
			}
			if (needsTrim && !cancelled) {
				cache = trimmed;
			}
		})();

		return () => {
			cancelled = true;
		};
	});

	$effect(() => {
		onPageChange?.(page);
	});

	function go(delta: number): void {
		page = Math.min(Math.max(page + delta, 1), Math.max(pageCount, 1));
	}

	function handleKeydown(event: KeyboardEvent): void {
		// Arrows only turn pages while focus is inside the reader, so arrows
		// elsewhere in the app keep their own meaning (spatial navigation,
		// form controls). The buttons below cover keyboard users who are not
		// focused inside it.
		const root = comic;
		if (!root || !root.contains(document.activeElement)) return;
		const target = event.target as HTMLElement | null;
		if (target && (target.tagName === 'INPUT' || target.tagName === 'SELECT')) return;
		// RTL comics invert the arrow semantics.
		switch (event.key) {
			case 'ArrowRight':
				event.preventDefault();
				event.stopPropagation();
				go(1);
				break;
			case 'ArrowLeft':
				event.preventDefault();
				event.stopPropagation();
				go(-1);
				break;
			default:
				break;
		}
	}

</script>

<svelte:window onkeydown={handleKeydown} />

<div class="comic" role="document" aria-label={`Page ${page} of ${pageCount}`} bind:this={comic}>
	{#if error}
		<p class="banner" role="alert">Could not load page: {error}</p>
	{:else if cache[page]}
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
		justify-content: center;
		height: 100%;
		flex: 1;
		min-height: 0;
		gap: 0.5rem;
		padding: 0.5rem 0;
	}

	.page {
		max-width: 100%;
		max-height: calc(100vh - 6.5rem);
		height: auto;
		width: auto;
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

	.banner {
		background: rgba(220, 38, 38, 0.15);
		border: 1px solid rgba(220, 38, 38, 0.4);
		color: #fca5a5;
		padding: 1rem 1.5rem;
		border-radius: var(--radius);
	}
</style>
