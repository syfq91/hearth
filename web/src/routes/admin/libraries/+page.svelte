<script lang="ts">
	import { api } from '../../../lib/api/client';
	import { invalidateAll } from '$app/navigation';
	import type { LibraryDetail } from '../../../lib/api/types';
	import type { PageProps } from './$types';
	import FolderBrowserModal from '../../../lib/components/FolderBrowserModal.svelte';

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
	let selectedPaths = $state<string[]>([]);
	let manualEntry = $state(false);
	let manualPathText = $state('');
	let browserOpen = $state(false);

	let creating = $state(false);
	let message = $state<string | null>(null);
	let error = $state<string | null>(null);
	let scanningId = $state<string | null>(null);

	/* ---- edit library state ---- */
	let editingId = $state<string | null>(null);
	let editName = $state('');
	let editType = $state<(typeof TYPES)[number]['value']>('movie');
	let editSelectedPaths = $state<string[]>([]);
	let editManualEntry = $state(false);
	let editManualPathText = $state('');
	let editBusy = $state(false);
	let editError = $state<string | null>(null);

	let browserMode = $state<'create' | 'edit'>('create');

	function startEdit(library: LibraryDetail): void {
		editingId = library.id;
		editName = library.name;
		editType = library.type as (typeof TYPES)[number]['value'];
		editSelectedPaths = [...library.paths];
		editManualEntry = false;
		editManualPathText = '';
		editError = null;
	}

	function cancelEdit(): void {
		editingId = null;
		editError = null;
		editManualEntry = false;
		editManualPathText = '';
	}

	function addEditManualPath(): void {
		const trimmed = editManualPathText.trim();
		if (!trimmed) return;
		if (!editSelectedPaths.includes(trimmed)) {
			editSelectedPaths = [...editSelectedPaths, trimmed];
		}
		editManualPathText = '';
	}

	function removeEditPath(pathToRemove: string): void {
		editSelectedPaths = editSelectedPaths.filter((p) => p !== pathToRemove);
	}

	async function saveEdit(libraryId: string): Promise<void> {
		if (editManualPathText.trim() && !editSelectedPaths.includes(editManualPathText.trim())) {
			editSelectedPaths = [...editSelectedPaths, editManualPathText.trim()];
			editManualPathText = '';
		}
		if (!editName.trim() || editSelectedPaths.length === 0) {
			editError = 'Please provide a library name and at least one folder path.';
			return;
		}
		editBusy = true;
		editError = null;
		message = null;
		try {
			const updated = await api.patch<LibraryDetail>(`/libraries/${libraryId}`, {
				body: {
					name: editName.trim(),
					type: editType,
					paths: editSelectedPaths
				}
			});
			libraries = libraries.map((lib) => (lib.id === libraryId ? updated : lib));
			message = `Library “${updated.name}” updated.`;
			editingId = null;
			await invalidateAll();
		} catch (e) {
			editError = e instanceof Error ? e.message : String(e);
		} finally {
			editBusy = false;
		}
	}

	function addManualPath(): void {
		const trimmed = manualPathText.trim();
		if (!trimmed) return;
		if (!selectedPaths.includes(trimmed)) {
			selectedPaths = [...selectedPaths, trimmed];
			if (!name.trim()) {
				const folderName = trimmed.split('/').filter(Boolean).pop();
				if (folderName) name = folderName;
			}
		}
		manualPathText = '';
	}

	function removePath(pathToRemove: string): void {
		selectedPaths = selectedPaths.filter((p) => p !== pathToRemove);
	}

	async function createLibrary(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		if (manualPathText.trim() && !selectedPaths.includes(manualPathText.trim())) {
			selectedPaths = [...selectedPaths, manualPathText.trim()];
			manualPathText = '';
		}
		if (!name.trim() || selectedPaths.length === 0) {
			error = 'Please provide a library name and at least one folder path.';
			return;
		}
		creating = true;
		message = null;
		error = null;
		try {
			await api.post('/libraries', { body: { name: name.trim(), type, paths: selectedPaths } });
			message = `Library “${name.trim()}” created.`;
			name = '';
			selectedPaths = [];
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

<svelte:head>
	<title>Libraries · Admin · Hearth</title>
</svelte:head>

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

		<div class="paths-section">
			<span class="paths-label">Folder Paths</span>

			{#if selectedPaths.length === 0}
				<div class="no-paths-box">
					<p class="muted">No folders selected yet.</p>
					<button
						type="button"
						class="browse-btn-highlight"
						onclick={() => {
							browserMode = 'create';
							browserOpen = true;
						}}
					>
						📁 Browse server folders…
					</button>
				</div>
			{:else}
				<ul class="selected-paths-list">
					{#each selectedPaths as p (p)}
						<li class="path-item">
							<span class="path-text">📁 {p}</span>
							<button
								type="button"
								class="remove-path-btn"
								onclick={() => removePath(p)}
								aria-label={`Remove path ${p}`}
								title="Remove path"
							>
								✕
							</button>
						</li>
					{/each}
				</ul>
				<div class="path-actions">
					<button
						type="button"
						class="ghost"
						onclick={() => {
							browserMode = 'create';
							browserOpen = true;
						}}
					>
						📁 Add another folder…
					</button>
					<button
						type="button"
						class="ghost text-btn"
						onclick={() => (manualEntry = !manualEntry)}
					>
						{manualEntry ? 'Hide manual entry' : '+ Type path manually'}
					</button>
				</div>
			{/if}

			{#if selectedPaths.length === 0}
				<div class="path-actions secondary">
					<button
						type="button"
						class="ghost text-btn"
						onclick={() => (manualEntry = !manualEntry)}
					>
						{manualEntry ? 'Hide manual entry' : 'Or enter path manually'}
					</button>
				</div>
			{/if}

			{#if manualEntry}
				<div class="manual-input-row">
					<input
						type="text"
						bind:value={manualPathText}
						placeholder="/media/movies"
						onkeydown={(e) => {
							if (e.key === 'Enter') {
								e.preventDefault();
								addManualPath();
							}
						}}
					/>
					<button type="button" class="ghost" onclick={addManualPath}>Add</button>
				</div>
			{/if}
		</div>

		<button type="submit" disabled={creating || selectedPaths.length === 0}>
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

<FolderBrowserModal
	open={browserOpen}
	initialPath={browserMode === 'edit'
		? editSelectedPaths[editSelectedPaths.length - 1] || ''
		: selectedPaths[selectedPaths.length - 1] || ''}
	onselect={(path) => {
		if (browserMode === 'edit') {
			if (!editSelectedPaths.includes(path)) {
				editSelectedPaths = [...editSelectedPaths, path];
			}
		} else {
			if (!selectedPaths.includes(path)) {
				selectedPaths = [...selectedPaths, path];
				if (!name.trim()) {
					const folderName = path.split('/').filter(Boolean).pop();
					if (folderName) name = folderName;
				}
			}
		}
		browserOpen = false;
	}}
	onclose={() => (browserOpen = false)}
/>

<section class="card" aria-labelledby="existing-heading">
	<h2 id="existing-heading">Libraries</h2>
	{#if libraries.length === 0}
		<p class="muted">No libraries yet.</p>
	{:else}
		<ul class="list">
			{#each libraries as library (library.id)}
				<li class="row {editingId === library.id ? 'is-editing' : ''}">
					{#if editingId === library.id}
						<form
							class="edit-form stack"
							onsubmit={(e) => {
								e.preventDefault();
								void saveEdit(library.id);
							}}
						>
							<div class="edit-header">
								<h3>Edit library</h3>
								<button
									type="button"
									class="close-edit-btn"
									onclick={cancelEdit}
									aria-label="Cancel editing"
									title="Cancel"
								>
									✕
								</button>
							</div>

							<label>
								<span>Name</span>
								<input type="text" bind:value={editName} required maxlength={128} />
							</label>

							<label>
								<span>Type</span>
								<select bind:value={editType}>
									{#each TYPES as option (option.value)}
										<option value={option.value}>{option.label}</option>
									{/each}
								</select>
							</label>

							<div class="paths-section">
								<span class="paths-label">Folder Paths</span>

								{#if editSelectedPaths.length === 0}
									<div class="no-paths-box">
										<p class="muted">No folders selected yet. A library needs at least one folder path.</p>
										<button
											type="button"
											class="browse-btn-highlight"
											onclick={() => {
												browserMode = 'edit';
												browserOpen = true;
											}}
										>
											📁 Browse server folders…
										</button>
									</div>
								{:else}
									<ul class="selected-paths-list">
										{#each editSelectedPaths as p (p)}
											<li class="path-item">
												<span class="path-text">📁 {p}</span>
												<button
													type="button"
													class="remove-path-btn"
													onclick={() => removeEditPath(p)}
													aria-label={`Remove path ${p}`}
													title="Remove path"
												>
													✕
												</button>
											</li>
										{/each}
									</ul>
									<div class="path-actions">
										<button
											type="button"
											class="ghost"
											onclick={() => {
												browserMode = 'edit';
												browserOpen = true;
											}}
										>
											📁 Add another folder…
										</button>
										<button
											type="button"
											class="ghost text-btn"
											onclick={() => (editManualEntry = !editManualEntry)}
										>
											{editManualEntry ? 'Hide manual entry' : '+ Type path manually'}
										</button>
									</div>
								{/if}

								{#if editSelectedPaths.length === 0}
									<div class="path-actions secondary">
										<button
											type="button"
											class="ghost text-btn"
											onclick={() => (editManualEntry = !editManualEntry)}
										>
											{editManualEntry ? 'Hide manual entry' : 'Or enter path manually'}
										</button>
									</div>
								{/if}

								{#if editManualEntry}
									<div class="manual-input-row">
										<input
											type="text"
											bind:value={editManualPathText}
											placeholder="/media/movies"
											onkeydown={(e) => {
												if (e.key === 'Enter') {
													e.preventDefault();
													addEditManualPath();
												}
											}}
										/>
										<button type="button" class="ghost" onclick={addEditManualPath}>Add</button>
									</div>
								{/if}
							</div>

							{#if editError}
								<p class="err" role="alert">{editError}</p>
							{/if}

							<div class="actions edit-actions">
								<button
									type="submit"
									disabled={editBusy || editSelectedPaths.length === 0 || !editName.trim()}
								>
									{editBusy ? 'Saving…' : 'Save changes'}
								</button>
								<button type="button" class="ghost" onclick={cancelEdit} disabled={editBusy}>
									Cancel
								</button>
							</div>
						</form>
					{:else}
						<div class="lib">
							<strong>{library.name}</strong>
							<span class="chip">{library.type}</span>
							<p class="paths">{library.paths.join(', ')}</p>
						</div>
						<div class="actions">
							<button
								type="button"
								class="ghost"
								onclick={() => startEdit(library)}
							>
								Edit
							</button>
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
					{/if}
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
		gap: 0.85rem;
		max-width: 34rem;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.85rem;
		color: var(--muted);
	}

	input,
	select {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.5rem 0.6rem;
		color: var(--text);
		font: inherit;
	}

	.paths-section {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
	}

	.paths-label {
		font-size: 0.85rem;
		color: var(--muted);
	}

	.no-paths-box {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 0.5rem;
		padding: 0.85rem 1rem;
		border: 1px dashed var(--border);
		border-radius: var(--radius);
		background: var(--surface-2);
	}

	.no-paths-box p {
		margin: 0;
		font-size: 0.85rem;
	}

	.browse-btn-highlight {
		background: var(--accent);
		color: var(--accent-contrast);
		border: 0;
		border-radius: var(--radius);
		padding: 0.45rem 1rem;
		font-weight: 600;
		cursor: pointer;
		font-size: 0.85rem;
	}

	.browse-btn-highlight:hover {
		opacity: 0.9;
	}

	.selected-paths-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}

	.path-item {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.4rem 0.75rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		font-family: ui-monospace, monospace;
		font-size: 0.85rem;
	}

	.path-text {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.remove-path-btn {
		background: transparent;
		border: 0;
		color: var(--muted);
		cursor: pointer;
		padding: 0.15rem 0.4rem;
		border-radius: 4px;
		font-size: 0.85rem;
	}

	.remove-path-btn:hover {
		color: var(--danger);
		background: var(--surface);
	}

	.path-actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-top: 0.25rem;
	}

	.path-actions.secondary {
		margin-top: 0;
	}

	.text-btn {
		font-size: 0.8rem;
		color: var(--muted);
	}

	.manual-input-row {
		display: flex;
		gap: 0.5rem;
		margin-top: 0.25rem;
	}

	.manual-input-row input {
		flex: 1;
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
		margin-top: 0.5rem;
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

	.row.is-editing {
		display: block;
		background: var(--surface-2);
		padding: 1rem 1.25rem 1.25rem;
		border-color: var(--accent);
	}

	.edit-form {
		width: 100%;
		max-width: 34rem;
	}

	.edit-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 0.25rem;
	}

	.edit-header h3 {
		font-size: 0.95rem;
		margin: 0;
	}

	.close-edit-btn {
		background: transparent;
		border: 0;
		color: var(--muted);
		cursor: pointer;
		font-size: 1rem;
		padding: 0.2rem 0.4rem;
		border-radius: 4px;
	}

	.close-edit-btn:hover {
		color: var(--text);
		background: var(--surface);
	}

	.edit-actions {
		display: flex;
		gap: 0.5rem;
		margin-top: 0.5rem;
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
		font: inherit;
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
