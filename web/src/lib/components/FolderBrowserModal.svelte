<script lang="ts">
	import { api } from '../api/client';
	import type { FilesystemBrowseResponse, FilesystemEntry } from '../api/types';

	interface Props {
		open: boolean;
		initialPath?: string;
		onselect: (path: string) => void;
		onclose: () => void;
	}

	let { open = false, initialPath, onselect, onclose }: Props = $props();

	let dialogEl = $state<HTMLDialogElement | null>(null);
	let currentPath = $state<string>('');
	let parentPath = $state<string | null>(null);
	let entries = $state<FilesystemEntry[]>([]);
	let selectedPath = $state<string>('');
	let loading = $state<boolean>(false);
	let error = $state<string | null>(null);

	$effect(() => {
		if (open) {
			dialogEl?.showModal();
			void loadPath(initialPath || '');
		} else {
			dialogEl?.close();
		}
	});

	async function loadPath(path: string): Promise<void> {
		loading = true;
		error = null;
		try {
			const query = path ? `?path=${encodeURIComponent(path)}` : '';
			const res = await api.get<FilesystemBrowseResponse>(`/admin/filesystem${query}`);
			currentPath = res.current_path;
			parentPath = res.parent_path ?? null;
			entries = res.entries ?? [];
			selectedPath = res.current_path;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	function handleSelect(): void {
		if (selectedPath) {
			onselect(selectedPath);
		}
	}

	// Build breadcrumb segments from currentPath
	let breadcrumbs = $derived.by(() => {
		if (!currentPath) return [];
		const parts = currentPath.split('/').filter(Boolean);
		let accumulated = '';
		return parts.map((part) => {
			accumulated += '/' + part;
			return { name: part, path: accumulated };
		});
	});
</script>

<dialog
	bind:this={dialogEl}
	class="folder-dialog"
	oncancel={(e) => {
		e.preventDefault();
		onclose();
	}}
	onclick={(e) => {
		if (e.target === dialogEl) onclose();
	}}
>
	<div class="modal-content">
		<header class="modal-header">
			<h3>Select Folder</h3>
			<button type="button" class="close-btn" aria-label="Close" onclick={onclose}>✕</button>
		</header>

		<!-- Breadcrumb & Nav -->
		<div class="nav-bar">
			<button
				type="button"
				class="nav-btn"
				disabled={!parentPath || loading}
				onclick={() => parentPath && void loadPath(parentPath)}
				title="Go up to parent folder"
			>
				⬆ Up
			</button>
			<nav class="breadcrumbs" aria-label="Folder breadcrumbs">
				<button
					type="button"
					class="crumb-btn root-btn"
					onclick={() => void loadPath('')}
					title="Root media directory"
				>
					media
				</button>
				{#each breadcrumbs as crumb, i}
					{#if i > 0 || crumb.name !== 'media'}
						<span class="separator">/</span>
						{#if i === breadcrumbs.length - 1}
							<span class="crumb-current">{crumb.name}</span>
						{:else}
							<button
								type="button"
								class="crumb-btn"
								onclick={() => void loadPath(crumb.path)}
							>
								{crumb.name}
							</button>
						{/if}
					{/if}
				{/each}
			</nav>
		</div>

		<!-- Directory Listing -->
		<div class="list-container">
			{#if loading}
				<div class="status-msg">Loading folders…</div>
			{:else if error}
				<div class="status-msg err">
					<p>{error}</p>
					<button type="button" class="nav-btn" onclick={() => void loadPath(currentPath || '')}>
						Retry
					</button>
				</div>
			{:else if entries.length === 0}
				<div class="status-msg muted">No subfolders found in this directory.</div>
			{:else}
				<ul class="folder-list">
					{#each entries as entry (entry.path)}
						<li
							class="folder-row"
							class:selected={selectedPath === entry.path}
							onclick={() => {
								selectedPath = entry.path;
							}}
							ondblclick={() => void loadPath(entry.path)}
							onkeydown={(e) => {
								if (e.key === 'Enter') void loadPath(entry.path);
							}}
							role="option"
							aria-selected={selectedPath === entry.path}
							tabindex="0"
						>
							<div class="folder-info">
								<span class="folder-icon">📁</span>
								<span class="folder-name">{entry.name}</span>
							</div>
							<div class="folder-actions">
								<button
									type="button"
									class="open-btn"
									onclick={(e) => {
										e.stopPropagation();
										void loadPath(entry.path);
									}}
									title="Open folder"
								>
									Open →
								</button>
							</div>
						</li>
					{/each}
				</ul>
			{/if}
		</div>

		<!-- Footer selection & buttons -->
		<footer class="modal-footer">
			<div class="selection-display">
				<span class="muted">Selected:</span>
				<code class="path-code" title={selectedPath}>{selectedPath || 'None'}</code>
			</div>
			<div class="footer-actions">
				<button type="button" class="ghost-btn" onclick={onclose}>Cancel</button>
				<button
					type="button"
					class="primary-btn"
					disabled={!selectedPath || loading}
					onclick={handleSelect}
				>
					Select this folder
				</button>
			</div>
		</footer>
	</div>
</dialog>

<style>
	.folder-dialog {
		background: var(--surface);
		color: var(--text);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0;
		max-width: 38rem;
		width: 90vw;
		box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
	}

	.folder-dialog::backdrop {
		background: rgba(0, 0, 0, 0.65);
		backdrop-filter: blur(2px);
	}

	.modal-content {
		display: flex;
		flex-direction: column;
		height: 32rem;
		max-height: 85vh;
	}

	.modal-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.85rem 1.25rem;
		border-bottom: 1px solid var(--border);
	}

	.modal-header h3 {
		font-size: 1.05rem;
		margin: 0;
	}

	.close-btn {
		background: transparent;
		border: 0;
		color: var(--muted);
		font-size: 1.1rem;
		cursor: pointer;
		padding: 0.2rem 0.4rem;
		border-radius: 4px;
	}

	.close-btn:hover {
		color: var(--text);
		background: var(--surface-2);
	}

	.nav-bar {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.6rem 1.25rem;
		background: var(--surface-2);
		border-bottom: 1px solid var(--border);
	}

	.nav-btn {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		color: var(--text);
		padding: 0.25rem 0.65rem;
		font-size: 0.8rem;
		cursor: pointer;
		flex-shrink: 0;
	}

	.nav-btn:hover:not(:disabled) {
		background: var(--surface-2);
	}

	.nav-btn:disabled {
		opacity: 0.45;
		cursor: default;
	}

	.breadcrumbs {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.25rem;
		font-family: ui-monospace, monospace;
		font-size: 0.8rem;
		overflow-x: auto;
	}

	.crumb-btn {
		background: transparent;
		border: 0;
		color: var(--accent);
		cursor: pointer;
		padding: 0.15rem 0.3rem;
		border-radius: 4px;
		font: inherit;
	}

	.crumb-btn:hover {
		text-decoration: underline;
		background: var(--surface);
	}

	.crumb-current {
		color: var(--text);
		font-weight: 600;
		padding: 0.15rem 0.3rem;
	}

	.separator {
		color: var(--muted);
	}

	.list-container {
		flex: 1;
		overflow-y: auto;
		padding: 0.5rem 0.75rem;
	}

	.status-msg {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		height: 100%;
		padding: 2rem;
		color: var(--muted);
		font-size: 0.9rem;
		gap: 0.75rem;
	}

	.status-msg.err {
		color: var(--danger);
	}

	.folder-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}

	.folder-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.5rem 0.75rem;
		border-radius: 6px;
		cursor: pointer;
		user-select: none;
		border: 1px solid transparent;
		transition: background 0.1s ease;
	}

	.folder-row:hover {
		background: var(--surface-2);
	}

	.folder-row.selected {
		background: var(--surface-2);
		border-color: var(--accent);
	}

	.folder-info {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		min-width: 0;
	}

	.folder-icon {
		font-size: 1.1rem;
		flex-shrink: 0;
	}

	.folder-name {
		font-weight: 500;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.open-btn {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 4px;
		color: var(--muted);
		padding: 0.2rem 0.55rem;
		font-size: 0.75rem;
		cursor: pointer;
	}

	.open-btn:hover {
		color: var(--text);
		border-color: var(--accent);
	}

	.modal-footer {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 0.75rem;
		padding: 0.75rem 1.25rem;
		border-top: 1px solid var(--border);
		background: var(--surface);
	}

	.selection-display {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		min-width: 0;
		max-width: 60%;
	}

	.path-code {
		font-family: ui-monospace, monospace;
		font-size: 0.8rem;
		background: var(--surface-2);
		padding: 0.2rem 0.45rem;
		border-radius: 4px;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.footer-actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-left: auto;
	}

	.ghost-btn {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.45rem 0.95rem;
		cursor: pointer;
		font: inherit;
		color: var(--text);
	}

	.ghost-btn:hover {
		background: var(--surface);
	}

	.primary-btn {
		background: var(--accent);
		color: var(--accent-contrast);
		border: 0;
		border-radius: var(--radius);
		padding: 0.45rem 1.1rem;
		font-weight: 600;
		cursor: pointer;
		font: inherit;
	}

	.primary-btn:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.muted {
		color: var(--muted);
	}
</style>
