<script lang="ts">
	interface Props {
		/** Current 1-indexed page. */
		page: number;
		/** Highest valid page (always at least 1). */
		totalPages: number;
		/** Total number of items across all pages. */
		total: number;
		/** Called with the requested page when a control is activated. */
		onPage: (page: number) => void;
		/** Disable the controls while a page is loading. */
		disabled?: boolean;
		/** Singular noun used in the status line, e.g. "book". */
		itemLabel?: string;
		/** Accessible name for the navigation landmark. */
		label?: string;
	}

	let {
		page,
		totalPages,
		total,
		onPage,
		disabled = false,
		itemLabel = 'item',
		label = 'Pagination'
	}: Props = $props();

	// A window of page numbers around the current page, with gaps for long lists.
	function pageList(current: number, count: number): (number | 'gap')[] {
		if (count <= 9) return Array.from({ length: count }, (_, index) => index + 1);
		const result: (number | 'gap')[] = [];
		const add = (value: number | 'gap') => {
			if (result[result.length - 1] !== value) result.push(value);
		};
		add(1);
		const start = Math.max(2, current - 2);
		const end = Math.min(count - 1, current + 2);
		if (start > 2) add('gap');
		for (let n = start; n <= end; n += 1) add(n);
		if (end < count - 1) add('gap');
		add(count);
		return result;
	}

	let pages = $derived(pageList(page, totalPages));
</script>

{#if total > 0}
	<p class="page-status">
		Page {page} of {totalPages} · {total} {total === 1 ? itemLabel : `${itemLabel}s`}
	</p>
	{#if totalPages > 1}
		<nav class="pagination" aria-label={label}>
			<button
				type="button"
				class="page-btn"
				onclick={() => onPage(page - 1)}
				disabled={disabled || page <= 1}
			>
				Previous
			</button>
			{#each pages as entry}
				{#if entry === 'gap'}
					<span class="page-gap" aria-hidden="true">…</span>
				{:else}
					<button
						type="button"
						class="page-btn"
						class:current={entry === page}
						aria-current={entry === page ? 'page' : undefined}
						onclick={() => onPage(entry)}
						{disabled}
					>
						{entry}
					</button>
				{/if}
			{/each}
			<button
				type="button"
				class="page-btn"
				onclick={() => onPage(page + 1)}
				disabled={disabled || page >= totalPages}
			>
				Next
			</button>
		</nav>
	{/if}
{/if}

<style>
	.pagination {
		display: flex;
		flex-wrap: wrap;
		justify-content: center;
		align-items: center;
		gap: 0.4rem;
		padding: 1.25rem 0 0.25rem;
	}

	.page-btn {
		min-width: 2.25rem;
		padding: 0.4rem 0.6rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		cursor: pointer;
	}

	.page-btn:hover:not(:disabled):not(.current) {
		background: var(--surface);
	}

	.page-btn.current {
		background: var(--accent);
		border-color: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
	}

	.page-btn:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.page-gap {
		color: var(--muted);
		padding: 0 0.15rem;
	}

	.page-status {
		text-align: center;
		color: var(--muted);
		font-size: 0.85rem;
		margin: 0.5rem 0 0;
	}
</style>
