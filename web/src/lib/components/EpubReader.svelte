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
	import { ChevronLeft, ChevronRight } from '@lucide/svelte';
	import { isTap, tapZoneAt, type Point, type TapZone } from '../reader/tap-zones';

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
		/** Middle-third tap toggles the host page's header. */
		onToggleHeader?: () => void;
	}

	let { bookUrl, initialCfi = null, onRelocate, onToggleHeader }: Props = $props();

	let host = $state<HTMLElement | null>(null);
	let ready = $state(false);
	let error = $state<string | null>(null);
	/** Reading position (0..1) from the last relocate, for the controls. */
	let fraction = $state(0);
	/** Current TOC label and section index from the last relocate. */
	let chapter = $state('');
	let section = $state({ current: 1, total: 1 });

	// The foliate instance lives outside Svelte's reactivity: it is an
	// imperative custom element held for the lifetime of the component.
	let view: FoliateView | null = null;

	function turnPage(delta: 1 | -1): void {
		if (!view) return;
		if (delta === 1) void view.next();
		else void view.prev();
	}

	// foliate renders pages inside iframes, whose clicks do not bubble to the
	// host. Listen on each loaded document as well as on the shell, then use
	// the same third-of-the-page tap logic in both places.
	let pointerStart: (Point & { zone: TapZone }) | null = null;

	function beginPointer(event: PointerEvent, rect: DOMRect): void {
		if (event.button !== 0) return;
		if ((event.target as HTMLElement | null)?.closest('button')) return;
		pointerStart = {
			x: event.clientX,
			y: event.clientY,
			zone: tapZoneAt(event.clientX, rect)
		};
	}

	function endPointer(event: PointerEvent): void {
		const start = pointerStart;
		pointerStart = null;
		if (!start || !isTap(start, event)) return;
		if (start.zone === 'prev') turnPage(-1);
		else if (start.zone === 'next') turnPage(1);
		else onToggleHeader?.();
	}

	// foliate replaces iframe documents between sections, so track the
	// listeners we add and detach them when the reader closes.
	const docListeners: Array<{ doc: Document; down: (e: PointerEvent) => void; up: (e: PointerEvent) => void }> = [];

	function listenOnDocument(doc: Document): void {
		if (docListeners.some((entry) => entry.doc === doc)) return;
		const down = (event: PointerEvent): void => {
			// Let foliate handle links and text selection instead of turning a page.
			const target = event.target as Element | null;
			if (target?.closest('a[href]')) return;
			if (doc.getSelection()?.isCollapsed === false) return;
			beginPointer(event, doc.documentElement.getBoundingClientRect());
		};
		const up = (event: PointerEvent): void => endPointer(event);
		doc.addEventListener('pointerdown', down);
		doc.addEventListener('pointerup', up);
		docListeners.push({ doc, down, up });
	}

	// foliate-js is pure ESM with a custom-element side effect; load it only in
	// the browser so the static build never tries to SSR it.
	onMount(() => {
		let disposed = false;

		const handleRelocate = (event: Event) => {
			const detail = (
				event as CustomEvent<{
					cfi?: string;
					fraction?: number;
					tocItem?: { label?: string } | null;
					section?: { current: number; total: number };
				}>
			).detail;
			if (!detail) return;
			const percentage = Math.round((detail.fraction ?? 0) * 10000) / 100;
			fraction = detail.fraction ?? 0;
			chapter = detail.tocItem?.label ?? `Section ${(detail.section?.current ?? 0) + 1}`;
			if (detail.section) section = detail.section;
			onRelocate?.(detail.cfi ?? '', percentage);
		};

		const handleLoad = (event: Event) => {
			const { doc } = (event as CustomEvent<{ doc?: Document }>).detail ?? {};
			if (doc) listenOnDocument(doc);
		};

		(async () => {
			try {
				await import('foliate-js/view.js');
				if (disposed || !host) return;
				view = document.createElement('foliate-view') as FoliateView;
				view.style.display = 'block';
				view.style.height = '100%';
				view.addEventListener('relocate', handleRelocate);
				view.addEventListener('load', handleLoad);
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
			view?.removeEventListener('load', handleLoad);
			for (const { doc, down, up } of docListeners) {
				doc.removeEventListener('pointerdown', down);
				doc.removeEventListener('pointerup', up);
			}
			docListeners.length = 0;
			view?.close();
			view?.remove();
		};
	});
</script>

<div
	class="epub-shell"
	role="presentation"
	onpointerdown={(event) => {
		if ((event.target as HTMLElement | null)?.closest('button')) return;
		beginPointer(event, event.currentTarget.getBoundingClientRect());
	}}
	onpointerup={(event) => endPointer(event)}
	onpointercancel={() => (pointerStart = null)}
>
	<div class="epub" bind:this={host}>
		{#if error}
			<p class="banner" role="alert">Could not open this book: {error}</p>
		{:else if !ready}
			<p class="status" role="status">Opening book…</p>
		{/if}
	</div>

	<button
		type="button"
		class="edge-nav prev"
		onclick={() => turnPage(-1)}
		disabled={!ready}
		aria-label="Previous page"
		title="Previous page"
	>
		<ChevronLeft size={28} />
	</button>
	<button
		type="button"
		class="edge-nav next"
		onclick={() => turnPage(1)}
		disabled={!ready}
		aria-label="Next page"
		title="Next page"
	>
		<ChevronRight size={28} />
	</button>
</div>

<div class="controls">
	<span class="percentage" aria-live="polite">
		{#if ready}{Math.round(fraction * 100)}%{/if}
	</span>
	<span class="chapter" title={chapter}>
		{#if ready}{chapter}{/if}
	</span>
	<span class="counter" aria-live="polite">
		{#if ready}{section.current} / {section.total}{/if}
	</span>
</div>

<style>
	.epub-shell {
		position: relative;
		display: flex;
		flex: 1;
		min-height: 0;
		width: 100%;
	}

	.epub {
		width: 100%;
		height: 100%;
		min-height: 0;
		flex: 1;
		background: var(--bg);
	}

	.edge-nav {
		position: absolute;
		top: 50%;
		transform: translateY(-50%);
		z-index: 2;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2.75rem;
		height: 2.75rem;
		padding: 0;
		background: rgba(0, 0, 0, 0.45);
		border: 1px solid var(--border);
		border-radius: 999px;
		color: #fff;
		cursor: pointer;
		opacity: 0.75;
		transition:
			opacity 0.15s ease,
			background 0.15s ease;
	}

	.edge-nav:hover:not(:disabled) {
		opacity: 1;
		background: rgba(0, 0, 0, 0.7);
	}

	.edge-nav:disabled {
		opacity: 0.25;
		cursor: default;
	}

	.edge-nav.prev {
		left: 0.75rem;
	}

	.edge-nav.next {
		right: 0.75rem;
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
		gap: 1rem;
		flex-shrink: 0;
		padding: 0.75rem 1rem;
	}

	.percentage,
	.counter {
		flex: 1;
		color: var(--muted);
		font-variant-numeric: tabular-nums;
	}

	.percentage {
		text-align: left;
	}

	.counter {
		text-align: right;
	}

	.chapter {
		flex: 0 1 auto;
		min-width: 0;
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
		text-align: center;
		color: var(--text);
	}

</style>
