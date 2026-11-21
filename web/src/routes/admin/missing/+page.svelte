<script lang="ts">
	import { api } from '../../../lib/api/client';
	import type { MissingFilePage, MissingFileResponse } from '../../../lib/api/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let files = $state<MissingFileResponse[]>([]);
	let total = $state<number>(0);
	let limit = $state<number>(50);
	let offset = $state<number>(0);
	let loading = $state<boolean>(false);
	let error = $state<string | null>(null);

	$effect(() => {
		files = data.page.items ?? [];
		total = data.page.total ?? 0;
		limit = data.page.limit ?? 50;
		offset = data.page.offset ?? 0;
	});

	async function fetchPage(nextOffset: number): Promise<void> {
		loading = true;
		error = null;
		try {
			const result = await api.get<MissingFilePage>('/admin/missing-files', {
				query: { limit, offset: nextOffset },
				cache: false
			});
			files = result.items ?? [];
			total = result.total ?? 0;
			limit = result.limit ?? 50;
			offset = result.offset ?? nextOffset;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	function formatDateTime(iso: string): string {
		try {
			return new Date(iso).toLocaleString([], {
				year: 'numeric',
				month: 'short',
				day: 'numeric',
				hour: '2-digit',
				minute: '2-digit'
			});
		} catch {
			return iso;
		}
	}

	let start = $derived(total === 0 ? 0 : offset + 1);
	let end = $derived(Math.min(offset + limit, total));
	let hasPrev = $derived(offset > 0);
	let hasNext = $derived(offset + limit < total);
</script>

<svelte:head>
	<title>Missing media · Admin · Hearth</title>
</svelte:head>

<h2>Missing media</h2>
<p class="intro">
	Files that were removed from disk and soft-deleted by the scanner. They stay in the catalog until
	the grace period passes and a later scan removes them.
</p>

{#if error}
	<p class="err" role="alert">{error}</p>
{/if}

{#if files.length === 0}
	<p class="muted">No missing media files found.</p>
{:else}
	<div class="table-wrap">
		<table>
			<thead>
				<tr>
					<th>Title</th>
					<th>Type</th>
					<th>Library</th>
					<th>Path</th>
					<th>Missing since</th>
				</tr>
			</thead>
			<tbody>
				{#each files as file (file.id)}
					<tr>
						<td>{file.item_title}</td>
						<td><span class="chip">{file.item_type}</span></td>
						<td>{file.library_name}</td>
						<td><code>{file.path}</code></td>
						<td>{formatDateTime(file.missing_since)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

<div class="pagination">
	<button
		type="button"
		class="ghost"
		onclick={() => fetchPage(Math.max(0, offset - limit))}
		disabled={!hasPrev || loading}
	>
		Prev
	</button>
	<span class="muted">
		{total === 0 ? 'Showing 0 of 0' : `Showing ${start}–${end} of ${total}`}
	</span>
	<button
		type="button"
		class="ghost"
		onclick={() => fetchPage(offset + limit)}
		disabled={!hasNext || loading}
	>
		Next
	</button>
</div>

<style>
	.intro {
		color: var(--muted);
		max-width: 70ch;
		margin: 0 0 1.25rem;
	}

	h2 {
		font-size: 1.3rem;
		margin: 0 0 0.25rem;
	}

	.table-wrap {
		overflow-x: auto;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
	}

	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.85rem;
	}

	th,
	td {
		text-align: left;
		padding: 0.5rem 0.75rem;
		border-bottom: 1px solid var(--border);
		vertical-align: top;
	}

	th {
		color: var(--muted);
		font-weight: 600;
		white-space: nowrap;
	}

	tbody tr:last-child td {
		border-bottom: none;
	}

	.chip {
		display: inline-block;
		font-size: 0.72rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0 0.5rem;
	}

	code {
		font-size: 0.78rem;
		color: var(--muted);
		overflow-wrap: anywhere;
	}

	.pagination {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-top: 1rem;
		font-size: 0.85rem;
	}

	.ghost {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.35rem 0.85rem;
		color: var(--text);
		cursor: pointer;
	}

	.ghost:hover:not(:disabled) {
		background: var(--surface);
		border-color: var(--accent);
	}

	button:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.muted {
		color: var(--muted);
	}

	.err {
		color: var(--danger);
		font-size: 0.9rem;
	}
</style>
