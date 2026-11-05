<script lang="ts">
	import { api } from '../../lib/api/client';
	import type { BookListResponse } from '../../lib/api/types';
	import BookCover from '../../lib/components/BookCover.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	type Book = BookListResponse['items'][number];

	let initial = $state<BookListResponse | null>(null);
	let error = $state<string | null>(null);
	let extraItems = $state<BookListResponse['items']>([]);
	let total = $state(0);
	let loadingMore = $state(false);

	let items = $derived([...(initial?.items ?? []), ...extraItems]);
	let currentTotal = $derived(initial ? total || initial.total : 0);

	// The loader returns a promise so navigation is not held up by the request.
	$effect(() => {
		let cancelled = false;
		initial = null;
		error = null;
		void data.books
			.then((value) => {
				if (cancelled) return;
				initial = value;
				extraItems = [];
				total = value.total;
			})
			.catch((e) => {
				if (!cancelled) error = e instanceof Error ? e.message : String(e);
			});
		return () => {
			cancelled = true;
		};
	});

	async function loadMore() {
		if (loadingMore || items.length >= currentTotal) return;
		loadingMore = true;
		try {
			const res = await api.get<BookListResponse>('/books', {
				query: { limit: 96, offset: items.length }
			});
			extraItems = [...extraItems, ...res.items];
			total = res.total;
		} finally {
			loadingMore = false;
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
{:else if initial === null}
	<p class="muted" role="status">Loading books…</p>
{:else if items.length === 0}
	<p class="muted">No books yet.</p>
{:else}
	<ul class="grid">
		{#each items as book (book.id)}
			<li>
				<article class="book">
					<a href={`/books/${book.id}`} aria-label={book.title}>
						<BookCover coverImageId={book.cover_image_id} alt="" />
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
	{#if items.length < currentTotal}
		<div class="more">
			<button type="button" class="load-more" onclick={loadMore} disabled={loadingMore}>
				{loadingMore ? 'Loading…' : 'Load more'}
			</button>
		</div>
	{/if}
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

	.more {
		margin-top: 2rem;
		display: flex;
		justify-content: center;
	}

	.load-more {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.6rem 1.4rem;
		cursor: pointer;
	}

	.load-more:hover:not(:disabled) {
		background: var(--surface);
	}

	.load-more:disabled {
		opacity: 0.6;
		cursor: default;
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
