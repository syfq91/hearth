<script lang="ts">
	import { api } from '../../../lib/api/client';
	import { invalidateAll } from '$app/navigation';
	import type { LibraryDetail } from '../../../lib/api/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let libraries = $state<LibraryDetail[]>([]);
	$effect(() => {
		libraries = data.libraries;
	});

	const TYPES = [
		{ value: 'movie', label: 'Movies' },
		{ value: 'tv', label: 'TV shows' },
		{ value: 'music', label: 'Music' },
		{ value: 'book', label: 'Books' }
	] as const;

	let name = $state('');
	let type = $state<(typeof TYPES)[number]['value']>('movie');
	let pathsText = $state('');
	let creating = $state(false);
	let message = $state<string | null>(null);
	let error = $state<string | null>(null);
	let scanningId = $state<string | null>(null);

	async function createLibrary(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		const paths = pathsText
			.split('\n')
			.map((line) => line.trim())
			.filter(Boolean);
		if (!name.trim() || paths.length === 0) return;
		creating = true;
		message = null;
		error = null;
		try {
			await api.post('/libraries', { body: { name: name.trim(), type, paths } });
			message = `Library “${name.trim()}” created.`;
			name = '';
			pathsText = '';
			await invalidateAll();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			creating = false;
		}
	}

	async function scanLibrary(libraryId: string): Promise<void> {
		scanningId = libraryId;
		error = null;
		try {
			await api.post(`/admin/libraries/${libraryId}/scan`);
			message = 'Scan queued. Check the admin overview for job progress.';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			scanningId = null;
		}
	}

	async function removeLibrary(libraryId: string, libraryName: string): Promise<void> {
		if (!confirm(`Delete the library “${libraryName}”? Its files are not touched.`)) return;
		error = null;
		try {
			await api.del(`/libraries/${libraryId}`);
			libraries = libraries.filter((library) => library.id !== libraryId);
			message = `Library “${libraryName}” deleted.`;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}
</script>

<section class="card" aria-labelledby="create-heading">
	<h2 id="create-heading">Add a library</h2>
	<form class="stack" onsubmit={createLibrary}>
		<label>
			<span>Name</span>
			<input type="text" bind:value={name} required maxlength={128} placeholder="Movies" />
		</label>
		<label>
			<span>Type</span>
			<select bind:value={type}>
				{#each TYPES as option (option.value)}
					<option value={option.value}>{option.label}</option>
				{/each}
			</select>
		</label>
		<label>
			<span>Paths (one per line, absolute inside the container)</span>
			<textarea bind:value={pathsText} rows={3} required placeholder="/media/movies"></textarea>
		</label>
		<button type="submit" disabled={creating}>
			{creating ? 'Creating…' : 'Create library'}
		</button>
	</form>
	{#if message}
		<p class="ok" role="status">{message}</p>
	{/if}
	{#if error}
		<p class="err" role="alert">{error}</p>
	{/if}
</section>

<section class="card" aria-labelledby="existing-heading">
	<h2 id="existing-heading">Libraries</h2>
	{#if libraries.length === 0}
		<p class="muted">No libraries yet.</p>
	{:else}
		<ul class="list">
			{#each libraries as library (library.id)}
				<li class="row">
					<div class="lib">
						<strong>{library.name}</strong>
						<span class="chip">{library.type}</span>
						<p class="paths">{library.paths.join(', ')}</p>
					</div>
					<div class="actions">
						<button
							type="button"
							class="ghost"
							onclick={() => scanLibrary(library.id)}
							disabled={scanningId === library.id}
						>
							{scanningId === library.id ? 'Queuing…' : 'Scan'}
						</button>
						<button
							type="button"
							class="ghost danger"
							onclick={() => removeLibrary(library.id, library.name)}
						>
							Delete
						</button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 1.25rem;
		margin-bottom: 1.25rem;
	}

	.card h2 {
		font-size: 1.05rem;
		margin-bottom: 0.75rem;
	}

	.stack {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		max-width: 32rem;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.85rem;
		color: var(--muted);
	}

	input,
	select,
	textarea {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.5rem 0.6rem;
		color: var(--text);
		font: inherit;
	}

	textarea {
		resize: vertical;
		font-family: ui-monospace, monospace;
		font-size: 0.85rem;
	}

	button[type='submit'] {
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		border: 0;
		border-radius: var(--radius);
		padding: 0.55rem 1.2rem;
		cursor: pointer;
		align-self: flex-start;
	}

	button:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 0.75rem;
	}

	.row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.75rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
	}

	.lib {
		min-width: 0;
	}

	.paths {
		color: var(--muted);
		font-size: 0.8rem;
		margin: 0.25rem 0 0;
		font-family: ui-monospace, monospace;
	}

	.chip {
		font-size: 0.75rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0 0.5rem;
		margin-left: 0.35rem;
	}

	.actions {
		display: flex;
		gap: 0.5rem;
	}

	.ghost {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.4rem 0.9rem;
		cursor: pointer;
	}

	.ghost:hover:not(:disabled) {
		background: var(--surface);
	}

	.danger {
		border-color: var(--danger);
		color: var(--danger);
	}

	.muted {
		color: var(--muted);
	}

	.ok {
		color: var(--accent);
		font-size: 0.9rem;
		margin-top: 0.75rem;
	}

	.err {
		color: var(--danger);
		font-size: 0.9rem;
		margin-top: 0.75rem;
	}
</style>
