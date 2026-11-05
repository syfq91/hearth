<script lang="ts">
	import BookCover from '../../../lib/components/BookCover.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const readerFormats = $derived(
		(data.book.formats ?? []).filter((format) => ['epub', 'pdf', 'cbz', 'cbr'].includes(format))
	);

	function readerHref(format: string): string {
		return `/books/${data.book.id}/read?format=${format}`;
	}

	const progress = $derived(data.book.progress);
</script>

<svelte:head>
	<title>{data.book.title} · Hearth</title>
</svelte:head>

<article class="book">
	<div class="art">
		<BookCover coverImageId={data.book.cover_image_id} alt="" width={400} />
	</div>

	<div class="info">
		<nav class="crumbs" aria-label="Breadcrumb">
			<a href="/books">Books</a>
		</nav>
		<h1>{data.book.title}</h1>
		{#if data.book.author}
			<p class="author">{data.book.author.name}</p>
		{/if}
		{#if data.book.series}
			<p class="series">
				{data.book.series.name}{data.book.series.index != null ? ` · Part ${data.book.series.index}` : ''}
			</p>
		{/if}
		<p class="meta">
			{[data.book.year, data.book.page_count ? `${data.book.page_count} pages` : null]
				.filter(Boolean)
				.join(' · ')}
		</p>

		{#if progress}
			<div class="progress-block">
				<div
					class="progress"
					role="progressbar"
					aria-valuenow={Math.round(progress.percentage)}
					aria-valuemin="0"
					aria-valuemax="100"
					aria-label="Reading progress"
				>
					<span style:width="{progress.percentage}%"></span>
				</div>
				<p class="progress-label">
					{progress.completed ? 'Completed' : `${Math.round(progress.percentage)}% read`}
				</p>
			</div>
		{/if}

		{#if readerFormats.length > 0}
			<div class="actions">
				{#each readerFormats as format (format)}
					<a class="read" href={readerHref(format)}>
						{progress && !progress.completed ? 'Resume' : 'Read'} {format.toUpperCase()}
					</a>
				{/each}
			</div>
		{:else}
			<p class="muted">No readable format available for this book.</p>
		{/if}

		{#if data.book.overview}
			<p class="overview">{data.book.overview}</p>
		{/if}
	</div>
</article>

<style>
	.book {
		display: grid;
		grid-template-columns: minmax(180px, 260px) 1fr;
		gap: 2rem;
		align-items: start;
	}

	.art {
		border-radius: var(--radius);
		overflow: hidden;
	}

	.crumbs {
		font-size: 0.9rem;
		color: var(--muted);
		margin-bottom: 0.5rem;
	}

	.info h1 {
		font-size: 1.7rem;
		margin-bottom: 0.25rem;
	}

	.author {
		color: var(--muted);
		margin: 0 0 0.25rem;
	}

	.series {
		color: var(--muted);
		font-size: 0.9rem;
		margin: 0 0 0.5rem;
	}

	.meta {
		color: var(--muted);
		font-size: 0.9rem;
		margin: 0 0 1rem;
	}

	.progress-block {
		max-width: 20rem;
		margin-bottom: 1rem;
	}

	.progress {
		height: 5px;
		background: var(--surface-2);
		border-radius: 3px;
		overflow: hidden;
	}

	.progress span {
		display: block;
		height: 100%;
		background: var(--accent);
	}

	.progress-label {
		color: var(--muted);
		font-size: 0.85rem;
		margin: 0.35rem 0 0;
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
		margin-bottom: 1.25rem;
	}

	.read {
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		border-radius: var(--radius);
		padding: 0.6rem 1.4rem;
	}

	.read:hover {
		text-decoration: none;
		filter: brightness(1.05);
	}

	.overview {
		max-width: 60ch;
		margin: 1rem 0 0;
	}

	.muted {
		color: var(--muted);
	}

	@media (max-width: 720px) {
		.book {
			grid-template-columns: 1fr;
		}

		.art {
			max-width: 200px;
		}
	}
</style>
