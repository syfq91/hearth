<script lang="ts">
	import BookCover from '../../lib/components/BookCover.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	function authorName(book: (typeof data.books.items)[number]): string {
		return book.author?.name ?? '';
	}
</script>

<h1>Books</h1>

{#if data.books.items.length === 0}
	<p class="muted">No books yet.</p>
{:else}
	<ul class="grid">
		{#each data.books.items as book (book.id)}
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

	.muted {
		color: var(--muted);
	}
</style>
