<script lang="ts">
	/**
	 * PDF reader on pdf.js canvas rendering.
	 *
	 * The document is streamed by URL (with range requests) instead of being
	 * downloaded in full, and only a small window around the current page is
	 * rendered at a time (§7.17.3 of the performance audit). The locator is
	 * `page:<n>` with percentage = page / total
	 * (docs/development/reader-integration.md §3).
	 */
	import { onMount } from 'svelte';
	import { getAccessToken } from '../api/client';
	import { pageWindow } from '../reader/page-window';

	interface Props {
		fileUrl: string;
		/** `page:<n>` locator to resume from. */
		initialLocator?: string | null;
		onPageChange?: (page: number, total: number) => void;
	}

	interface PdfPage {
		getViewport(opts: { scale: number }): { width: number; height: number };
		render(opts: {
			canvasContext: CanvasRenderingContext2D;
			canvas: HTMLCanvasElement;
			transform: number[];
		}): { promise: Promise<void> };
		cleanup(): void;
	}

	interface PdfDocument {
		numPages: number;
		getPage(n: number): Promise<PdfPage>;
		destroy(): Promise<void>;
	}

	let { fileUrl, initialLocator = null, onPageChange }: Props = $props();

	let container = $state<HTMLElement | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(true);
	let total = $state(0);
	let current = $state(1);
	let ready = $state(false);

	const WINDOW_BEFORE = 1;
	const WINDOW_AFTER = 2;

	const startPage = $derived.by(() => {
		const match = /^page:(\d+)$/.exec(initialLocator ?? '');
		return match ? Number.parseInt(match[1], 10) : 1;
	});

	let pdfDocument: PdfDocument | null = null;
	let rendered = new Map<number, { canvas: HTMLCanvasElement; page: PdfPage }>();
	let disposed = false;

	function reportPage(page: number): void {
		if (total > 0) onPageChange?.(page, total);
	}

	function goTo(page: number): void {
		const next = Math.min(Math.max(page, 1), Math.max(total, 1));
		if (next === current) return;
		current = next;
		reportPage(next);
	}

	async function renderWindow(center: number): Promise<void> {
		if (!container || !pdfDocument || disposed) return;
		const { first, last } = pageWindow(center, total, WINDOW_BEFORE, WINDOW_AFTER);

		for (const [pageNumber, entry] of rendered) {
			if (pageNumber < first || pageNumber > last) {
				entry.canvas.remove();
				entry.page.cleanup();
				rendered.delete(pageNumber);
			}
		}

		for (let n = first; n <= last; n += 1) {
			if (disposed || rendered.has(n)) continue;
			const page = await pdfDocument.getPage(n);
			if (disposed) {
				page.cleanup();
				return;
			}
			const viewport = page.getViewport({ scale: 1 });
			const canvas = document.createElement('canvas');
			const dpr = Math.min(window.devicePixelRatio || 1, 2);
			canvas.width = Math.floor(viewport.width * dpr);
			canvas.height = Math.floor(viewport.height * dpr);
			canvas.style.width = '100%';
			canvas.style.height = 'auto';
			canvas.setAttribute('aria-label', `Page ${n}`);
			const context = canvas.getContext('2d');
			if (!context) {
				page.cleanup();
				break;
			}
			await page.render({
				canvasContext: context,
				canvas,
				transform: dpr === 1 ? [] : [dpr, 0, 0, dpr, 0, 0]
			}).promise;
			if (disposed) {
				page.cleanup();
				return;
			}
			container.append(canvas);
			rendered.set(n, { canvas, page });
		}

		if (container) container.scrollTop = 0;
	}

	$effect(() => {
		if (ready && pdfDocument) void renderWindow(current);
	});

	onMount(() => {
		disposed = false;
		(async () => {
			try {
				const pdfjs = await import('pdfjs-dist');
				pdfjs.GlobalWorkerOptions.workerSrc = new URL(
					'pdfjs-dist/build/pdf.worker.min.mjs',
					import.meta.url
				).toString();

				const accessToken = getAccessToken();
				const task = pdfjs.getDocument({
					url: fileUrl,
					httpHeaders: accessToken ? { Authorization: `Bearer ${accessToken}` } : {}
				});
				const doc = (await task.promise) as unknown as PdfDocument;
				if (disposed) {
					await doc.destroy();
					return;
				}
				pdfDocument = doc;
				total = doc.numPages;
				current = Math.min(Math.max(startPage, 1), Math.max(total, 1));
				loading = false;
				ready = true;
				reportPage(current);
			} catch (e) {
				if (!disposed) error = e instanceof Error ? e.message : String(e);
			}
		})();

		return () => {
			disposed = true;
			ready = false;
			rendered.clear();
			const doc = pdfDocument;
			pdfDocument = null;
			if (doc) void doc.destroy();
		};
	});
</script>

<div class="pdf">
	{#if error}
		<p class="banner" role="alert">Could not open this PDF: {error}</p>
	{:else if loading}
		<p class="status" role="status">Loading PDF…</p>
	{/if}

	<div class="pages" bind:this={container}></div>

	{#if total > 0}
		<div class="toolbar">
			<button
				type="button"
				onclick={() => goTo(current - 1)}
				disabled={current <= 1}
				aria-label="Previous page"
			>
				Previous
			</button>
			<p class="counter" aria-live="polite">{current} / {total}</p>
			<button
				type="button"
				onclick={() => goTo(current + 1)}
				disabled={current >= total}
				aria-label="Next page"
			>
				Next
			</button>
		</div>
	{/if}
</div>

<style>
	.pdf {
		display: flex;
		flex-direction: column;
		align-items: center;
		height: 100%;
		flex: 1;
		min-height: 0;
		overflow-y: auto;
		padding: 1.5rem 1rem;
		gap: 1rem;
	}

	.pages {
		width: min(100%, 56rem);
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.pages :global(canvas) {
		background: #fff;
		border-radius: var(--radius);
		box-shadow: 0 2px 12px rgba(0, 0, 0, 0.4);
	}

	.toolbar {
		display: flex;
		align-items: center;
		gap: 1rem;
		position: sticky;
		bottom: 0;
		background: var(--surface, #161b22);
		padding: 0.5rem 0.75rem;
		border-radius: var(--radius);
	}

	.toolbar button {
		background: var(--surface-dim, #0e1116);
		color: inherit;
		border: 1px solid var(--outline, #2b3441);
		border-radius: var(--radius);
		padding: 0.4rem 0.75rem;
		cursor: pointer;
	}

	.toolbar button:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.counter {
		color: var(--muted);
		font-variant-numeric: tabular-nums;
	}

	.status {
		color: var(--muted);
		padding: 2rem;
	}

	.banner {
		color: var(--danger);
		padding: 2rem;
	}
</style>
