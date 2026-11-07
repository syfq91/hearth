<script lang="ts">
	import ComicReader from '../../../../lib/components/ComicReader.svelte';
	import EpubReader from '../../../../lib/components/EpubReader.svelte';
	import PdfReader from '../../../../lib/components/PdfReader.svelte';
	import { ProgressSync } from '../../../../lib/reader/progress';
	import { withMediaToken } from '../../../../lib/media-token.svelte';
	import { goto } from '$app/navigation';
	import { X, Maximize, Minimize } from '@lucide/svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const bookId = $derived(data.book.id);
	const format = $derived(data.format);
	const progress = $derived(data.progress);

	let isNativeFullscreen = $state(false);

	// One syncer per mounted reader; cleanly disposed when switching books or unmounting.
	let sync = $state<ProgressSync | null>(null);
	$effect(() => {
		const currentSync = new ProgressSync(bookId);
		sync = currentSync;
		return () => {
			currentSync.dispose();
		};
	});

	/** Comic locator is `page:<n>`; EPUB locator is an EPUB CFI. */
	function parsePage(locator: string | null | undefined): number {
		const match = /^page:(\d+)$/.exec(locator ?? '');
		return match ? Number.parseInt(match[1], 10) : 1;
	}

	const isComic = $derived(format === 'cbz' || format === 'cbr');
	const isPdf = $derived(format === 'pdf');
	const isEpub = $derived(format === 'epub');

	// File URL only needed for epub and pdf (ComicReader resolves per-page).
	let fileUrl = $state<string | null>(null);
	$effect(() => {
		if (!format || isComic) return;
		let cancelled = false;
		void withMediaToken(`/api/v1/books/${bookId}/file/${format}`).then((resolved) => {
			if (!cancelled) fileUrl = resolved;
		});
		return () => {
			cancelled = true;
		};
	});

	function handleEpubRelocate(locator: string, percentage: number): void {
		sync?.update({ locator, percentage });
	}

	function handleComicPage(page: number): void {
		const total = data.book.page_count ?? 0;
		const percentage = total > 0 ? Math.round((page / total) * 10000) / 100 : 0;
		sync?.update({ locator: `page:${page}`, percentage });
	}

	function handlePdfPage(page: number, total: number): void {
		const percentage = total > 0 ? Math.round((page / total) * 10000) / 100 : 0;
		sync?.update({ locator: `page:${page}`, percentage });
	}

	async function toggleNativeFullscreen(): Promise<void> {
		try {
			if (!document.fullscreenElement) {
				await document.documentElement.requestFullscreen();
				isNativeFullscreen = true;
			} else {
				await document.exitFullscreen();
				isNativeFullscreen = false;
			}
		} catch {
			/* fullscreen might be restricted by browser policy */
		}
	}

	$effect(() => {
		const onFsChange = () => {
			isNativeFullscreen = Boolean(document.fullscreenElement);
		};
		const onKeydown = (event: KeyboardEvent) => {
			if (event.key === 'Escape' && !document.fullscreenElement) {
				void goto(`/books/${data.book.id}`);
			}
		};
		document.addEventListener('fullscreenchange', onFsChange);
		window.addEventListener('keydown', onKeydown);
		return () => {
			document.removeEventListener('fullscreenchange', onFsChange);
			window.removeEventListener('keydown', onKeydown);
		};
	});
</script>

<svelte:head>
	<title>{data.book.title} · Reading · Hearth</title>
</svelte:head>

<div class="book-reader-container">
	<header class="reader-header">
		<div class="reader-header-left">
			<a
				href={`/books/${data.book.id}`}
				class="close-button"
				aria-label="Close book"
				title="Close book (Esc)"
			>
				<X size={18} />
				<span>Close</span>
			</a>
			<span class="divider" aria-hidden="true">/</span>
			<a href={`/books/${data.book.id}`} class="crumbs" aria-label={data.book.title}>
				{data.book.title}
			</a>
		</div>

		<div class="reader-header-right">
			{#if format}
				<span class="format">{format.toUpperCase()}</span>
			{/if}
			<button
				type="button"
				class="fs-button"
				onclick={toggleNativeFullscreen}
				aria-label={isNativeFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
				title={isNativeFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
			>
				{#if isNativeFullscreen}
					<Minimize size={18} />
				{:else}
					<Maximize size={18} />
				{/if}
			</button>
		</div>
	</header>

	<div class="reader-viewport">
		{#if !format}
			<p class="muted">This book has no readable format.</p>
		{:else if isEpub && fileUrl}
			<EpubReader
				bookUrl={fileUrl}
				initialCfi={progress && !progress.completed ? progress.locator : null}
				onRelocate={handleEpubRelocate}
			/>
		{:else if isComic}
			<ComicReader
				bookId={bookId}
				pageCount={data.book.page_count ?? 0}
				startPage={parsePage(progress?.locator)}
				onPageChange={handleComicPage}
			/>
		{:else if isPdf && fileUrl}
			<PdfReader
				fileUrl={fileUrl}
				initialLocator={progress?.locator ?? null}
				onPageChange={handlePdfPage}
			/>
		{:else}
			<p class="muted">Preparing reader…</p>
		{/if}
	</div>
</div>

<style>
	.book-reader-container {
		display: flex;
		flex-direction: column;
		height: 100vh;
		width: 100%;
		background: var(--bg);
		overflow: hidden;
	}

	.reader-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		padding: 0.6rem 1.25rem;
		background: var(--surface);
		border-bottom: 1px solid var(--border);
		z-index: 20;
		flex-shrink: 0;
	}

	.reader-header-left {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		min-width: 0;
	}

	.close-button {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.35rem 0.75rem;
		color: var(--text);
		font-size: 0.85rem;
		font-weight: 500;
		cursor: pointer;
		text-decoration: none;
		transition:
			background 0.15s ease,
			border-color 0.15s ease,
			color 0.15s ease;
	}

	.close-button:hover {
		background: rgba(255, 107, 107, 0.15);
		border-color: var(--danger);
		color: var(--danger);
		text-decoration: none;
	}

	.divider {
		color: var(--border);
	}

	.crumbs {
		font-size: 0.95rem;
		font-weight: 500;
		color: var(--text);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.crumbs:hover {
		color: var(--accent);
	}

	.reader-header-right {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		flex-shrink: 0;
	}

	.fs-button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.4rem;
		color: var(--muted);
		cursor: pointer;
		transition:
			color 0.15s ease,
			border-color 0.15s ease;
	}

	.fs-button:hover {
		color: var(--text);
		border-color: var(--muted);
	}

	.format {
		font-size: 0.75rem;
		font-weight: 700;
		letter-spacing: 0.05em;
		color: var(--accent);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0.15rem 0.6rem;
	}

	.reader-viewport {
		flex: 1;
		min-height: 0;
		width: 100%;
		display: flex;
		flex-direction: column;
		overflow: hidden;
		position: relative;
	}

	.muted {
		color: var(--muted);
		padding: 2rem;
		text-align: center;
	}
</style>
