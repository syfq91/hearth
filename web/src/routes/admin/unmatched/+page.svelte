<script lang="ts">
	import { api } from '../../../lib/api/client';
	import { invalidateAll } from '$app/navigation';
	import type { SearchResultResponse, UnmatchedItemResponse } from '../../../lib/api/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let unmatched = $state<UnmatchedItemResponse[]>([]);
	$effect(() => {
		unmatched = data.unmatched;
	});

	let message = $state<string | null>(null);
	let error = $state<string | null>(null);

	/** itemId -> candidate list currently loaded for that item. */
	let candidates = $state<Record<string, SearchResultResponse[]>>({});
	/** itemId -> admin-edited search text (defaults to the item title). */
	let queries = $state<Record<string, string>>({});
	/** itemId -> chosen provider name (empty means all providers). */
	let selectedProviders = $state<Record<string, string>>({});
	let busyId = $state<string | null>(null);
	let searchingId = $state<string | null>(null);

	function searchText(item: UnmatchedItemResponse): string {
		return queries[item.id] ?? item.title;
	}

	async function searchCandidates(item: UnmatchedItemResponse): Promise<void> {
		const query = searchText(item).trim();
		if (!query) {
			error = 'Enter a search query.';
			return;
		}
		searchingId = item.id;
		error = null;
		try {
			const provider = selectedProviders[item.id];
			const results = await api.get<SearchResultResponse[]>('/metadata/search', {
				query: {
					query,
					year: item.year ?? undefined,
					provider: provider || undefined,
					item_id: item.id
				}
			});
			candidates = { ...candidates, [item.id]: results };
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			searchingId = null;
		}
	}

	async function applyMatch(
		itemId: string,
		candidate: SearchResultResponse
	): Promise<void> {
		busyId = itemId;
		error = null;
		try {
			await api.post(`/items/${itemId}/match`, {
				body: { provider: candidate.provider, provider_id: candidate.provider_id }
			});
			message = `Matched to “${candidate.title}”.`;
			unmatched = unmatched.filter((item) => item.id !== itemId);
			delete candidates[itemId];
			candidates = { ...candidates };
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busyId = null;
		}
	}

	async function rematch(itemId: string): Promise<void> {
		busyId = itemId;
		error = null;
		try {
			await api.post(`/admin/items/${itemId}/rematch`, { body: {} });
			message = 'Rematch queued.';
			await invalidateAll();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busyId = null;
		}
	}
</script>

<svelte:head>
	<title>Unmatched · Admin · Hearth</title>
</svelte:head>

<p class="intro">
	Items the matcher could not identify. Search metadata providers and pick the right match — open a
	candidate's source page to verify it first — or trigger a rematch with the automatic matcher.
</p>

{#if message}
	<p class="ok" role="status">{message}</p>
{/if}
{#if error}
	<p class="err" role="alert">{error}</p>
{/if}

{#if unmatched.length === 0}
	<p class="muted">Nothing unmatched. The catalog is fully identified.</p>
{:else}
	<ul class="list">
		{#each unmatched as item (item.id)}
			<li class="item">
				<div class="head">
					<strong>{item.title}</strong>
					<span class="chip">{item.type}</span>
					{#if item.year}<span class="chip">{item.year}</span>{/if}
					{#if item.match_error}
						<span class="chip err-chip" title={item.match_error}>matcher error</span>
					{/if}
				</div>

				{#if item.file_paths?.length}
					<div class="files">
						{#each item.file_paths ?? [] as path}
							<code>{path}</code>
						{/each}
					</div>
				{/if}

				<div class="search-row">
					<input
						type="text"
						aria-label={`Search text for ${item.title}`}
						placeholder="Search text"
						value={searchText(item)}
						oninput={(event) => (queries[item.id] = event.currentTarget.value)}
						disabled={searchingId === item.id}
					/>
					<select
						aria-label={`Metadata provider for ${item.title}`}
						value={selectedProviders[item.id] ?? ''}
						onchange={(event) => (selectedProviders[item.id] = event.currentTarget.value)}
						disabled={searchingId === item.id}
					>
						<option value="">All providers</option>
						{#each data.providers as provider}
							<option value={provider}>{provider}</option>
						{/each}
					</select>
				</div>

				<div class="actions">
					<button
						type="button"
						class="ghost"
						onclick={() => searchCandidates(item)}
						disabled={searchingId === item.id}
					>
						{searchingId === item.id ? 'Searching…' : 'Search metadata'}
					</button>
					<button
						type="button"
						class="ghost"
						onclick={() => rematch(item.id)}
						disabled={busyId === item.id}
					>
						Rematch
					</button>
				</div>

				{#if candidates[item.id]}
					{#if candidates[item.id].length === 0}
						<p class="muted">No candidates found.</p>
					{:else}
						<ul class="candidates">
							{#each candidates[item.id] as candidate (`${candidate.provider}:${candidate.provider_id}`)}
								<li class="candidate">
									<div class="candidate-info">
										<div class="candidate-title">
											{#if candidate.source_url}
												<a
													class="title-link"
													href={candidate.source_url}
													target="_blank"
													rel="noopener noreferrer"
													title={`Open ${candidate.title} on ${candidate.provider}`}
												>
													<strong>{candidate.title}</strong>
												</a>
											{:else}
												<strong>{candidate.title}</strong>
											{/if}
											{#if candidate.year}<span class="muted">({candidate.year})</span>{/if}
											{#if candidate.source_url}
												<a
													class="source-link"
													href={candidate.source_url}
													target="_blank"
													rel="noopener noreferrer"
													title={`Open source page on ${candidate.provider}`}
												>
													View source ↗
												</a>
											{/if}
										</div>
										<p class="provider">{candidate.provider} · {candidate.provider_id}</p>
									</div>
									<button
										type="button"
										class="apply"
										onclick={() => applyMatch(item.id, candidate)}
										disabled={busyId === item.id}
									>
										Use this match
									</button>
								</li>
							{/each}
						</ul>
					{/if}
				{/if}
			</li>
		{/each}
	</ul>
{/if}

<style>
	.intro {
		color: var(--muted);
		max-width: 60ch;
		margin-bottom: 1.25rem;
	}

	.list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 0.85rem;
	}

	.item {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.85rem;
		background: var(--surface);
	}

	.head {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		margin-bottom: 0.6rem;
	}

	.chip {
		font-size: 0.72rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0 0.5rem;
	}

	.err-chip {
		border-color: var(--danger);
		color: var(--danger);
	}

	.files {
		display: grid;
		gap: 0.2rem;
		margin-bottom: 0.6rem;
	}

	.files code {
		font-size: 0.78rem;
		color: var(--muted);
		overflow-wrap: anywhere;
	}

	.search-row {
		display: flex;
		gap: 0.5rem;
		margin-bottom: 0.5rem;
	}

	.search-row input,
	.search-row select {
		flex: 1;
		min-width: 0;
		background: var(--surface-2);
		color: inherit;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.35rem 0.6rem;
	}

	.search-row select {
		flex: 0 1 12rem;
	}

	.actions {
		display: flex;
		gap: 0.5rem;
		margin-bottom: 0.5rem;
	}

	.candidates {
		list-style: none;
		padding: 0;
		margin: 0.5rem 0 0;
		display: grid;
		gap: 0.5rem;
	}

	.candidate {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.5rem 0.6rem;
		border: 1px solid var(--border);
		border-radius: 6px;
	}

	.candidate-info {
		min-width: 0;
	}

	.candidate-title {
		display: flex;
		align-items: baseline;
		flex-wrap: wrap;
		gap: 0.35rem;
	}

	.title-link {
		color: inherit;
		text-decoration: none;
	}

	.title-link:hover strong,
	.source-link:hover {
		text-decoration: underline;
	}

	.source-link {
		color: var(--accent);
		font-size: 0.75rem;
		white-space: nowrap;
	}

	.source-link:focus-visible,
	.title-link:focus-visible {
		outline: 2px solid var(--accent);
		outline-offset: 2px;
	}

	.provider {
		color: var(--muted);
		font-size: 0.78rem;
		margin: 0;
		font-family: ui-monospace, monospace;
	}

	.ghost,
	.apply {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.35rem 0.85rem;
		cursor: pointer;
	}

	.ghost:hover:not(:disabled) {
		background: var(--surface);
	}

	.apply {
		background: var(--accent);
		color: var(--accent-contrast);
		border: 0;
		font-weight: 600;
		white-space: nowrap;
	}

	button:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.muted {
		color: var(--muted);
	}

	.ok {
		color: var(--accent);
		font-size: 0.9rem;
	}

	.err {
		color: var(--danger);
		font-size: 0.9rem;
	}
</style>
