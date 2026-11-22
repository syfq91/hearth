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
	import { DEFAULT_READER_SETTINGS, type ReaderSettings } from '../reader/settings';
	import type { TocItem } from '../reader/toc';

	interface FoliateRenderer extends HTMLElement {
		setStyles?(css: string): void;
	}

	interface FoliateView extends HTMLElement {
		open(url: string): Promise<void>;
		goTo(target: unknown): Promise<void>;
		next(): Promise<void>;
		prev(): Promise<void>;
		close(): void;
		renderer?: FoliateRenderer;
		book?: { toc?: TocItem[] };
	}

	interface Props {
		bookUrl: string;
		/** Server-side locator (EPUB CFI) to resume from, if any. */
		initialCfi?: string | null;
		onRelocate?: (locator: string, percentage: number) => void;
		/** Middle-third tap toggles the host page's header. */
		onToggleHeader?: () => void;
		/** Fires when a user turn actually changes the displayed page. */
		onPageTurned?: () => void;
		/** Header visibility; the edge buttons fade with the host chrome. */
		chromeVisible?: boolean;
		/** Display preferences applied to the rendered book. */
		settings?: ReaderSettings;
		/** Receives the book's table of contents once it is opened. */
		onToc?: (toc: TocItem[]) => void;
		/** Bound by the host page so the header TOC can jump to a chapter. */
		goToChapter?: (href: string) => void;
	}

	let {
		bookUrl,
		initialCfi = null,
		onRelocate,
		onToggleHeader,
		onPageTurned,
		chromeVisible = true,
		settings = DEFAULT_READER_SETTINGS,
		onToc,
		goToChapter = $bindable()
	}: Props = $props();

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

	async function turnPage(delta: 1 | -1): Promise<void> {
		if (!view) return;
		// Hide synchronously with the gesture so a quick middle tap right
		// after turning is not undone by the async relocate that follows.
		onPageTurned?.();
		if (delta === 1) await view.next();
		else await view.prev();
	}

	function styleFor(prefs: ReaderSettings): string {
		const palette = {
			light: { bg: '#ffffff', fg: '#1a1a1a', scheme: 'light' },
			sepia: { bg: '#f5ecd7', fg: '#4a3b2c', scheme: 'light' },
			dark: { bg: '#181a1b', fg: '#e6e6e6', scheme: 'dark' }
		}[prefs.theme];
		const family = {
			serif: "Georgia, 'Times New Roman', serif",
			sans: "system-ui, -apple-system, 'Segoe UI', sans-serif",
			mono: "ui-monospace, 'SFMono-Regular', Consolas, monospace"
		}[prefs.fontFamily];
		return `
			html {
				--theme-bg-color: ${palette.bg};
				--theme-fg-color: ${palette.fg};
				color-scheme: ${palette.scheme};
			}
			body {
				background-color: ${palette.bg} !important;
				color: ${palette.fg} !important;
				font-family: ${family} !important;
				font-size: ${prefs.fontSize}px !important;
			}
			p, li, blockquote, dd {
				font-family: ${family} !important;
				font-size: ${prefs.fontSize}px !important;
			}
		`;
	}

	const themeBackground = $derived(
		settings.theme === 'light'
			? '#ffffff'
			: settings.theme === 'sepia'
				? '#f5ecd7'
				: '#181a1b'
	);

	$effect(() => {
		if (!ready) return;
		view?.renderer?.setStyles?.(styleFor(settings));
		view?.renderer?.setAttribute('max-column-count', String(settings.columns));
	});

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

	/** Arrow keys turn pages anywhere in the reader, not just on focus. */
	function handleKeydown(event: KeyboardEvent): void {
		const target = event.target as HTMLElement | null;
		if (
			target &&
			(target.tagName === 'INPUT' ||
				target.tagName === 'SELECT' ||
				target.tagName === 'TEXTAREA' ||
				target.isContentEditable ||
				target.closest('[role="dialog"], [role="menu"]'))
		) {
			return;
		}
		if (event.key === 'ArrowRight') {
			event.preventDefault();
			turnPage(1);
		} else if (event.key === 'ArrowLeft') {
			event.preventDefault();
			turnPage(-1);
		}
	}

	// foliate replaces iframe documents between sections, so track the
	// listeners we add and detach them when the reader closes.
	const docListeners: Array<{
		doc: Document;
		down: (e: PointerEvent) => void;
		up: (e: PointerEvent) => void;
		key: (e: KeyboardEvent) => void;
	}> = [];

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
		const key = (event: KeyboardEvent): void => handleKeydown(event);
		doc.addEventListener('pointerdown', down);
		doc.addEventListener('pointerup', up);
		doc.addEventListener('keydown', key);
		docListeners.push({ doc, down, up, key });
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
			// foliate reports a zero-based section index; show a 1-based count.
			if (detail.section) {
				section = {
					current: detail.section.current + 1,
					total: detail.section.total
				};
			}
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
				goToChapter = (href: string) => {
					if (view) void view.goTo(href);
				};
				onToc?.(view.book?.toc ?? []);
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
			for (const { doc, down, up, key } of docListeners) {
				doc.removeEventListener('pointerdown', down);
				doc.removeEventListener('pointerup', up);
				doc.removeEventListener('keydown', key);
			}
			docListeners.length = 0;
			view?.close();
			view?.remove();
		};
	});
</script>

<svelte:window onkeydown={handleKeydown} />

<div
	class="epub-shell"
	role="presentation"
	style:--reader-bg={themeBackground}
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
		data-hidden={!chromeVisible}
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
		data-hidden={!chromeVisible}
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
		background: var(--reader-bg, var(--bg));
	}

	.epub {
		position: relative;
		width: 100%;
		height: 100%;
		min-height: 0;
		flex: 1;
		background: transparent;
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
			background 0.15s ease,
			visibility 0.15s ease;
	}

	.edge-nav:hover:not(:disabled) {
		opacity: 1;
		background: rgba(0, 0, 0, 0.7);
	}

	.edge-nav:disabled {
		opacity: 0.25;
		cursor: default;
	}

	/* The edge buttons follow the auto-hiding header instead of lingering over
	   a page the reader has settled on. */
	.edge-nav[data-hidden='true'] {
		visibility: hidden;
		opacity: 0;
		pointer-events: none;
	}

	/* Keep keyboard access: a button already focused when the chrome hides
	   must not vanish mid-navigation, like the header's :focus-within rule. */
	.edge-nav[data-hidden='true']:focus-visible {
		visibility: visible;
		opacity: 1;
		pointer-events: auto;
	}

	.edge-nav.prev {
		left: 0.75rem;
	}

	.edge-nav.next {
		right: 0.75rem;
	}

	/* Centred in the reading area so the floating header cannot cover it. */
	.status,
	.banner {
		position: absolute;
		top: 50%;
		left: 50%;
		transform: translate(-50%, -50%);
		margin: 0;
		padding: 1rem;
		text-align: center;
	}

	.status {
		color: var(--muted);
	}

	.banner {
		color: var(--danger);
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
