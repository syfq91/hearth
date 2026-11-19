<script lang="ts">
	import { api } from '../../lib/api/client';
	import type { BookListResponse } from '../../lib/api/types';
	import BookCover from '../../lib/components/BookCover.svelte';
	import Pagination from '../../lib/components/Pagination.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	type Book = BookListResponse['items'][number];

	const PAGE_SIZE = 48;

	let items = $state<Book[]>([]);
	let total = $state(0);
	let page = $state(1);
	let loaded = $state(false);
	let loading = $state(false);
	let error = $state<string | null>(null);

	let totalPages = $derived(Math.max(1, Math.ceil(total / PAGE_SIZE)));

	// The loader returns a promise so navigation is not held up by the request.
	$effect(() => {
		let cancelled = false;
		loaded = false;
		error = null;
		page = 1;
		void data.books
			.then((value) => {
				if (cancelled) return;
				items = value.items;
				total = value.total;
				loaded = true;
			})
			.catch((e) => {
				if (!cancelled) error = e instanceof Error ? e.message : String(e);
			});
		return () => {
			cancelled = true;
		};
	});

	function goToPage(next: number): void {
		const target = Math.min(Math.max(next, 1), totalPages);
		if (target === page) return;
		page = target;
		void loadPage();
	}

	async function loadPage(): Promise<void> {
		loading = true;
		error = null;
		try {
			const res = await api.get<BookListResponse>('/books', {
				query: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }
			});
			items = res.items;
			total = res.total;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	function authorName(book: Book): string {
		return book.author?.name ?? '';
	}
</script>

<svelte:head>
	<title>Books · Hearth</title>
</svelte:head>

<h1>Books</h1>

{#if error}
	<p class="banner" role="alert">{error}</p>
{:else if !loaded}
	<p class="muted" role="status">Loading books…</p>
{:else if items.length === 0}
	<p class="muted">No books yet.</p>
{:else}
	<ul class="grid">
		{#each items as book, index (book.id)}
			<li>
				<article class="book">
					<a href={`/books/${book.id}`} aria-label={book.title}>
						<BookCover coverImageId={book.cover_image_id} alt="" priority={index === 0} />
					</a>
					<h2 class="title">
						<a href={`/books/${book.id}`}>{book.title}</a>
					</h2>
					{#if authorName(book)}
						<p class="author">{authorName(book)}</p>
					{/if}
					{#if book.progress}
						<div
							class="progress"
							role="progressbar"
							aria-valuenow={Math.round(book.progress.percentage)}
							aria-valuemin="0"
							aria-valuemax="100"
							aria-label="Reading progress"
						>
							<span style:width="{book.progress.percentage}%"></span>
						</div>
					{/if}
				</article>
			</li>
		{/each}
	</ul>
	<Pagination
		{page}
		{totalPages}
		{total}
		onPage={goToPage}
		disabled={loading}
		itemLabel="book"
	/>
{/if}

<style>
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 1.25rem 1rem;
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.book {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}

	.title {
		font-size: 0.95rem;
		font-weight: 600;
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.author {
		color: var(--muted);
		font-size: 0.8rem;
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.progress {
		height: 3px;
		background: var(--surface-2);
		border-radius: 2px;
		overflow: hidden;
	}

	.progress span {
		display: block;
		height: 100%;
		background: var(--accent);
	}

	.banner {
		background: var(--surface);
		border: 1px solid var(--border);
		border-left: 3px solid var(--danger);
		border-radius: var(--radius);
		padding: 0.6rem 1rem;
		color: var(--muted);
	}

	.muted {
		color: var(--muted);
	}
</style>
