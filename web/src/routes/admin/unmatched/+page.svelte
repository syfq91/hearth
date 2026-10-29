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
	let busyId = $state<string | null>(null);
	let searchingId = $state<string | null>(null);

	async function searchCandidates(item: UnmatchedItemResponse): Promise<void> {
		searchingId = item.id;
		error = null;
		try {
			const results = await api.get<SearchResultResponse[]>('/metadata/search', {
				query: { query: item.title, year: item.year ?? undefined }
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
	Items the matcher could not identify. Search metadata providers and pick the right match, or
	trigger a rematch with the automatic matcher.
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
										<strong>{candidate.title}</strong>
										{#if candidate.year}<span class="muted"> ({candidate.year})</span>{/if}
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
