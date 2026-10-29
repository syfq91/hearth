<script lang="ts">
	/**
	 * PDF reader on pdf.js canvas rendering.
	 *
	 * Each page is rendered to a canvas at device-pixel-ratio resolution, and
	 * the locator is `page:<n>` with percentage = page / total
	 * (docs/READER_INTEGRATION.md §3). The document is fetched once with the
	 * bearer token, then parsed in memory.
	 */
	import { onMount } from 'svelte';
	import { getAccessToken } from '../api/client';

	interface Props {
		fileUrl: string;
		/** `page:<n>` locator to resume from. */
		initialLocator?: string | null;
		onPageChange?: (page: number, total: number) => void;
	}

	let { fileUrl, initialLocator = null, onPageChange }: Props = $props();

	let container = $state<HTMLElement | null>(null);
	let error = $state<string | null>(null);
	let loading = $state(true);
	let total = $state(0);
	let current = $state(1);

	const startPage = $derived.by(() => {
		const match = /^page:(\d+)$/.exec(initialLocator ?? '');
		return match ? Number.parseInt(match[1], 10) : 1;
	});

	onMount(() => {
		let disposed = false;
		let observer: IntersectionObserver | null = null;

		// Track which page is most visible so progress follows the reader.
		function observePage(canvas: HTMLCanvasElement, pageNumber: number): void {
			observer ??= new IntersectionObserver(
				(entries) => {
					for (const entry of entries) {
						if (entry.isIntersecting) {
							current = pageNumber;
							onPageChange?.(pageNumber, total);
						}
					}
				},
				{ threshold: 0.5 }
			);
			observer.observe(canvas);
		}

		async function renderAll(doc: {
			numPages: number;
			getPage: (n: number) => Promise<unknown>;
		}): Promise<void> {
			if (!container) return;
			container.replaceChildren();
			for (let n = 1; n <= doc.numPages; n += 1) {
				if (disposed) return;
				const page = (await doc.getPage(n)) as {
					getViewport: (opts: { scale: number }) => { width: number; height: number };
					render: (opts: {
						canvasContext: CanvasRenderingContext2D;
						canvas: HTMLCanvasElement;
						transform: number[];
					}) => { promise: Promise<void> };
				};
				const viewport = page.getViewport({ scale: 1 });
				const canvas = document.createElement('canvas');
				const dpr = Math.min(window.devicePixelRatio || 1, 2);
				canvas.width = Math.floor(viewport.width * dpr);
				canvas.height = Math.floor(viewport.height * dpr);
				canvas.style.width = '100%';
				canvas.style.height = 'auto';
				canvas.setAttribute('aria-label', `Page ${n}`);
				const context = canvas.getContext('2d');
				if (!context) break;
				await page.render({
					canvasContext: context,
					canvas,
					transform: dpr === 1 ? [] : [dpr, 0, 0, dpr, 0, 0]
				}).promise;
				container.append(canvas);
				observePage(canvas, n);
			}
		}

		(async () => {
			try {
				const pdfjs = await import('pdfjs-dist');
				// Route the worker through Vite's asset pipeline.
				pdfjs.GlobalWorkerOptions.workerSrc = new URL(
					'pdfjs-dist/build/pdf.worker.min.mjs',
					import.meta.url
				).toString();

				const accessToken = getAccessToken();
				const response = await fetch(fileUrl, {
					headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {}
				});
				if (!response.ok) throw new Error(`Could not fetch the PDF (${response.status})`);
				const bytes = await response.arrayBuffer();
				if (disposed) return;
				const pdf = await pdfjs.getDocument({ data: bytes }).promise;
				total = pdf.numPages;
				loading = false;
				current = Math.min(Math.max(startPage, 1), total);
				await renderAll(pdf);
			} catch (e) {
				if (!disposed) error = e instanceof Error ? e.message : String(e);
			}
		})();

		return () => {
			disposed = true;
			observer?.disconnect();
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
		<p class="counter" aria-live="polite">{current} / {total}</p>
	{/if}
</div>

<style>
	.pdf {
		display: flex;
		flex-direction: column;
		align-items: center;
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
