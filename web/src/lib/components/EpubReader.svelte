<script lang="ts">
	/**
	 * EPUB reader built on foliate-js's `<foliate-view>` custom element.
	 *
	 * The element is a plain custom element with a JS API (open/goTo/prev/next
	 * and a `relocate` event), so it is driven imperatively here rather than
	 * through Svelte bindings. The book URL carries a media token because the
	 * reader fetches the archive directly (no Authorization header).
	 */
	import { onMount } from 'svelte';

	interface FoliateView extends HTMLElement {
		open(url: string): Promise<void>;
		goTo(target: unknown): Promise<void>;
		next(): Promise<void>;
		prev(): Promise<void>;
		close(): void;
	}

	interface Props {
		bookUrl: string;
		/** Server-side locator (EPUB CFI) to resume from, if any. */
		initialCfi?: string | null;
		onRelocate?: (locator: string, percentage: number) => void;
	}

	let { bookUrl, initialCfi = null, onRelocate }: Props = $props();

	let host = $state<HTMLElement | null>(null);
	let ready = $state(false);
	let error = $state<string | null>(null);
	/** Reading position (0..1) from the last relocate, for the controls. */
	let fraction = $state(0);

	// The foliate instance lives outside Svelte's reactivity: it is an
	// imperative custom element held for the lifetime of the component.
	let view: FoliateView | null = null;

	function turnPage(delta: 1 | -1): void {
		if (!view) return;
		if (delta === 1) void view.next();
		else void view.prev();
	}

	// foliate-js is pure ESM with a custom-element side effect; load it only in
	// the browser so the static build never tries to SSR it.
	onMount(() => {
		let disposed = false;

		const handleRelocate = (event: Event) => {
			const detail = (event as CustomEvent<{ cfi?: string; fraction?: number }>).detail;
			if (!detail) return;
			const percentage = Math.round((detail.fraction ?? 0) * 10000) / 100;
			fraction = detail.fraction ?? 0;
			onRelocate?.(detail.cfi ?? '', percentage);
		};

		(async () => {
			try {
				await import('foliate-js/view.js');
				if (disposed || !host) return;
				view = document.createElement('foliate-view') as FoliateView;
				view.style.display = 'block';
				view.style.height = '100%';
				view.addEventListener('relocate', handleRelocate);
				host.append(view);
				await view.open(bookUrl);
				if (disposed) return;
				// Resume at the saved CFI, else start at the text beginning.
				if (initialCfi) await view.goTo(initialCfi);
				else await view.next();
				ready = true;
			} catch (e) {
				error = e instanceof Error ? e.message : String(e);
			}
		})();

		return () => {
			disposed = true;
			view?.removeEventListener('relocate', handleRelocate);
			view?.close();
			view?.remove();
		};
	});
</script>

<div class="epub" bind:this={host}>
	{#if error}
		<p class="banner" role="alert">Could not open this book: {error}</p>
	{:else if !ready}
		<p class="status" role="status">Opening book…</p>
	{/if}
</div>

<div class="controls">
	<button type="button" class="ghost" onclick={() => turnPage(-1)} disabled={!ready}>
		← Previous
	</button>
	<span class="position" aria-live="polite">
		{#if ready}{Math.round(fraction * 100)}%{/if}
	</span>
	<button type="button" class="ghost" onclick={() => turnPage(1)} disabled={!ready}>
		Next →
	</button>
</div>

<style>
	.epub {
		width: 100%;
		height: 100%;
		min-height: 60vh;
		background: var(--bg);
	}

	.status {
		color: var(--muted);
		padding: 1rem;
	}

	.banner {
		color: var(--danger);
		padding: 1rem;
	}

	.controls {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 1rem;
		padding: 0.75rem 0;
	}

	.position {
		color: var(--muted);
		font-variant-numeric: tabular-nums;
		min-width: 4ch;
		text-align: center;
	}

	.ghost {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.45rem 1rem;
		cursor: pointer;
		color: inherit;
	}

	.ghost:disabled {
		opacity: 0.5;
		cursor: default;
	}
</style>
