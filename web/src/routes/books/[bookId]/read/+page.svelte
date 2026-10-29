<script lang="ts">
	import ComicReader from '../../../../lib/components/ComicReader.svelte';
	import EpubReader from '../../../../lib/components/EpubReader.svelte';
	import PdfReader from '../../../../lib/components/PdfReader.svelte';
	import { ProgressSync } from '../../../../lib/reader/progress';
	import { withMediaToken } from '../../../../lib/media-token.svelte';
	import { onDestroy, untrack } from 'svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const bookId = $derived(data.book.id);
	const format = $derived(data.format);
	const progress = $derived(data.progress);

	// One syncer per mounted reader; the route remounts per book.
	const sync = untrack(() => new ProgressSync(bookId));
	onDestroy(() => sync.dispose());

	/** Comic locator is `page:<n>`; EPUB locator is an EPUB CFI. */
	function parsePage(locator: string | null | undefined): number {
		const match = /^page:(\d+)$/.exec(locator ?? '');
		return match ? Number.parseInt(match[1], 10) : 1;
	}

	// Comic page URLs need a media token; resolved lazily by ComicReader via
	// withMediaToken, so only the book file URL is built here.
	let fileUrl = $state<string | null>(null);
	$effect(() => {
		if (!format) return;
		let cancelled = false;
		void withMediaToken(`/api/v1/books/${bookId}/file/${format}`).then((resolved) => {
			if (!cancelled) fileUrl = resolved;
		});
		return () => {
			cancelled = true;
		};
	});

	function handleEpubRelocate(locator: string, percentage: number): void {
		sync.update({ locator, percentage });
	}

	function handleComicPage(page: number): void {
		const total = data.book.page_count ?? 0;
		const percentage = total > 0 ? Math.round((page / total) * 10000) / 100 : 0;
		sync.update({ locator: `page:${page}`, percentage });
	}

	function handlePdfPage(page: number, total: number): void {
		const percentage = total > 0 ? Math.round((page / total) * 10000) / 100 : 0;
		sync.update({ locator: `page:${page}`, percentage });
	}

	const isComic = $derived(format === 'cbz' || format === 'cbr');
	const isPdf = $derived(format === 'pdf');
	const isEpub = $derived(format === 'epub');
</script>

<header class="head">
	<nav class="crumbs" aria-label="Breadcrumb">
		<a href={`/books/${data.book.id}`}>{data.book.title}</a>
	</nav>
	{#if format}
		<span class="format">{format.toUpperCase()}</span>
	{/if}
</header>

<main class="reader">
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
</main>

<style>
	.head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 1rem;
	}

	.crumbs {
		font-size: 0.95rem;
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

	.reader {
		padding: 0;
	}

	.muted {
		color: var(--muted);
	}
</style>
