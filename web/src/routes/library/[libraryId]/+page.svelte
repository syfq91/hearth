<script lang="ts">
	import { Folder } from '@lucide/svelte';
	import { api } from '../../../lib/api/client';
	import MediaCard from '../../../lib/components/MediaCard.svelte';
	import Poster from '../../../lib/components/Poster.svelte';
	import type { ItemPage, LibraryFolderPage } from '../../../lib/api/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	// Book libraries keep their on-disk directory tree: the grid shows one
	// folder at a time, starting at the library root.
	const isBook = $derived(data.library.type === 'book');
	const isMusic = $derived(data.library.type === 'music');
	let folder = $state('');
	let folders = $state<LibraryFolderPage['folders']>([]);
	let foldersLoading = $state(false);
	let folderSegments = $derived(folder ? folder.split('/') : []);

	type SortKey = 'title' | 'added' | 'year' | 'rating';

	const SORTS: { value: SortKey; label: string }[] = [
		{ value: 'title', label: 'Title' },
		{ value: 'added', label: 'Date added' },
		{ value: 'year', label: 'Year' },
		{ value: 'rating', label: 'Rating' }
	];

	let sort = $state<SortKey>('title');
	let order = $state<'asc' | 'desc'>('asc');
	let genre = $state('');
	let year = $state('');
	let watched = $state<'' | 'true' | 'false'>('');
	let debouncedGenre = $state('');
	let debouncedYear = $state('');

	// Text/number filters wait for a short pause before hitting the API.
	$effect(() => {
		const nextGenre = genre;
		const nextYear = year;
		const timer = setTimeout(() => {
			debouncedGenre = nextGenre;
			debouncedYear = nextYear;
		}, 250);
		return () => clearTimeout(timer);
	});

	const PAGE_SIZE = 48;

	let items = $state<ItemPage['items']>([]);
	let total = $state(0);
	let page = $state(1);
	let loading = $state(false);
	let error = $state<string | null>(null);

	let totalPages = $derived(Math.max(1, Math.ceil(total / PAGE_SIZE)));

	// Changing any filter or folder starts again at the first page.
	let filterKey = $derived(
		`${sort}|${order}|${debouncedGenre}|${debouncedYear}|${watched}|${folder}`
	);

	$effect(() => {
		filterKey;
		page = 1;
	});

	$effect(() => {
		void loadPage();
	});

	$effect(() => {
		void loadFolders();
	});

	async function loadPage(): Promise<void> {
		// Read every input before the await so Svelte tracks them: any filter or
		// page change reloads the list.
		const currentPage = page;
		const currentSort = sort;
		const currentOrder = order;
		const currentGenre = debouncedGenre;
		const currentYear = debouncedYear;
		const currentWatched = watched;
		const currentFolder = folder;
		const isBookLibrary = isBook;

		loading = true;
		error = null;
		try {
			const result = await api.get<ItemPage>(`/libraries/${data.library.id}/items`, {
				query: {
					sort: currentSort,
					order: currentOrder,
					limit: PAGE_SIZE,
					offset: (currentPage - 1) * PAGE_SIZE,
					genre: currentGenre || undefined,
					year: currentYear ? Number(currentYear) : undefined,
					watched: currentWatched === '' ? undefined : currentWatched === 'true',
					folder: isBookLibrary && currentFolder ? currentFolder : undefined
				}
			});
			items = result.items;
			total = result.total;
			// Filters can shrink the result set while an older page is selected.
			const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE));
			if (currentPage > lastPage && page === currentPage) page = lastPage;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
			items = [];
			total = 0;
		} finally {
			loading = false;
		}
	}

	function goToPage(next: number): void {
		page = Math.min(Math.max(next, 1), totalPages);
	}

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

	async function loadFolders(): Promise<void> {
		if (!isBook) return;
		foldersLoading = true;
		folders = [];
		try {
			const page = await api.get<LibraryFolderPage>(
				`/libraries/${data.library.id}/folders`,
				{ query: { path: folder || undefined } }
			);
			folders = page.folders;
		} catch {
			folders = [];
		} finally {
			foldersLoading = false;
		}
	}

	function openFolder(path: string): void {
		folder = path;
	}

	// Genre suggestions come from the cards already on screen, so the filter
	// works before any dedicated genres endpoint exists.
	let genreSuggestions = $derived(
		[...new Set(items.flatMap((item) => item.genres))].sort((a, b) => a.localeCompare(b))
	);
</script>

<svelte:head>
	<title>{data.library.name} · Hearth</title>
</svelte:head>

<nav class="crumbs" aria-label="Breadcrumb">
	<a href="/library">Libraries</a>
	<span aria-hidden="true">/</span>
	<span>{data.library.name}</span>
</nav>

<h1>{data.library.name}</h1>

{#if isBook && folder}
	<nav class="folder-crumbs" aria-label="Folder breadcrumb">
		<button type="button" class="crumb" onclick={() => (folder = '')}>All folders</button>
		{#each folderSegments as segment, index (index)}
			<span aria-hidden="true">/</span>
			<button
				type="button"
				class="crumb"
				onclick={() => (folder = folderSegments.slice(0, index + 1).join('/'))}
			>
				{segment}
			</button>
		{/each}
	</nav>
{/if}

<form class="filters" aria-label="Filter items">
	<label>
		<span>Sort by</span>
		<select bind:value={sort}>
			{#each SORTS as option (option.value)}
				<option value={option.value}>{option.label}</option>
			{/each}
		</select>
	</label>
	<label>
		<span>Order</span>
		<select bind:value={order}>
			<option value="asc">Ascending</option>
			<option value="desc">Descending</option>
		</select>
	</label>
	<label>
		<span>Genre</span>
		<input
			type="search"
			list="genre-options"
			bind:value={genre}
			placeholder="Any"
			aria-describedby="genre-hint"
		/>
		<datalist id="genre-options">
			{#each genreSuggestions as option (option)}
				<option value={option}></option>
			{/each}
		</datalist>
		<span id="genre-hint" class="visually-hidden">
			Type a genre; suggestions appear from the loaded items.
		</span>
	</label>
	<label>
		<span>Year</span>
		<input type="number" min="1800" max="2100" bind:value={year} placeholder="Any" />
	</label>
	<label>
		<span>Watched</span>
		<select bind:value={watched}>
			<option value="">Any</option>
			<option value="false">Unwatched</option>
			<option value="true">Watched</option>
		</select>
	</label>
</form>

{#if error}
	<p class="banner" role="alert">{error}</p>
{/if}

{#if isBook && (folders.length > 0 || foldersLoading)}
	<section class="folders" aria-label="Folders">
		<h2 class="section-title">Folders</h2>
		{#if folders.length > 0}
			<ul class="folder-grid">
				{#each folders as entry (entry.path)}
					<li>
						<button
							type="button"
							class="folder-card"
							onclick={() => openFolder(entry.path)}
							aria-label={`Open folder ${entry.name}`}
						>
							<div class="folder-poster-wrap">
								{#if entry.poster}
									<Poster image={entry.poster} alt="" aspect="2 / 3" />
								{:else}
									<div class="folder-fallback-poster" aria-hidden="true">
										<Folder size={36} />
									</div>
								{/if}
								<span class="folder-badge" aria-hidden="true">
									<Folder size={13} />
								</span>
							</div>
							<span class="folder-name" title={entry.name}>{entry.name}</span>
							<span class="folder-count">
								{entry.item_count}
								{entry.item_count === 1 ? 'book' : 'books'}
							</span>
						</button>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="muted" role="status">Loading folders…</p>
		{/if}
	</section>
{/if}

{#if loading}
	<p class="muted" role="status">Loading…</p>
{:else if items.length === 0 && !error && !(isBook && folders.length > 0)}
	<p class="muted">No items match these filters.</p>
{:else if items.length > 0}
	<section class="books" aria-label="Books">
		{#if isBook}
			<h2 class="section-title">Books</h2>
		{/if}
		<ul class="grid" class:music-grid={isMusic}>
			{#each items as item (item.id)}
				<li><MediaCard item={item} aspect={isMusic ? '1 / 1' : undefined} /></li>
			{/each}
		</ul>
		<p class="page-status">Page {page} of {totalPages} · {total} items</p>
		{#if totalPages > 1}
			<nav class="pagination" aria-label="Pagination">
				<button
					type="button"
					class="page-btn"
					onclick={() => goToPage(page - 1)}
					disabled={page <= 1 || loading}
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
							onclick={() => goToPage(entry)}
							disabled={loading}
						>
							{entry}
						</button>
					{/if}
				{/each}
				<button
					type="button"
					class="page-btn"
					onclick={() => goToPage(page + 1)}
					disabled={page >= totalPages || loading}
				>
					Next
				</button>
			</nav>
		{/if}
	</section>
{/if}

<style>
	.crumbs {
		display: flex;
		gap: 0.5rem;
		color: var(--muted);
		font-size: 0.9rem;
		margin-bottom: 0.5rem;
	}

	.filters {
		display: flex;
		flex-wrap: wrap;
		gap: 1rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.9rem 1rem;
		margin-bottom: 1.25rem;
	}

	.filters label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.85rem;
		color: var(--muted);
	}

	.filters select,
	.filters input {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.35rem 0.5rem;
		min-width: 8rem;
	}

	.banner {
		background: var(--surface);
		border: 1px solid var(--border);
		border-left: 3px solid var(--danger);
		border-radius: var(--radius);
		padding: 0.6rem 1rem;
		color: var(--muted);
	}

	.folder-crumbs {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.4rem;
		color: var(--muted);
		font-size: 0.9rem;
		margin-bottom: 0.75rem;
	}

	.crumb {
		background: none;
		border: 0;
		color: inherit;
		font: inherit;
		padding: 0;
		cursor: pointer;
	}

	.crumb:hover {
		color: var(--accent);
		text-decoration: underline;
	}

	.section-title {
		font-size: 1rem;
		font-weight: 600;
		margin: 0 0 0.75rem;
	}

	.folders {
		margin-bottom: 1.75rem;
	}

	.folder-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 1.25rem 1rem;
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.folder-card {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		width: 100%;
		background: transparent;
		border: 0;
		padding: 0;
		color: inherit;
		text-align: left;
		cursor: pointer;
		position: relative;
	}

	.folder-card:hover .folder-poster-wrap {
		filter: brightness(1.08);
	}

	.folder-poster-wrap {
		position: relative;
		width: 100%;
		border-radius: var(--radius);
		overflow: hidden;
		transition: filter 0.15s ease;
	}

	.folder-fallback-poster {
		width: 100%;
		aspect-ratio: 2 / 3;
		border-radius: var(--radius);
		background: var(--surface-2);
		border: 1px solid var(--border);
		display: flex;
		align-items: center;
		justify-content: center;
		color: var(--muted);
	}

	.folder-badge {
		position: absolute;
		top: 0.5rem;
		right: 0.5rem;
		background: rgba(22, 27, 34, 0.85);
		backdrop-filter: blur(4px);
		border: 1px solid var(--border);
		color: var(--accent);
		border-radius: 6px;
		padding: 0.25rem 0.35rem;
		display: flex;
		align-items: center;
		justify-content: center;
		box-shadow: 0 2px 6px rgba(0, 0, 0, 0.4);
		pointer-events: none;
	}

	.folder-name {
		font-size: 0.95rem;
		font-weight: 600;
		margin: 0;
		line-height: 1.3;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 100%;
	}

	.folder-count {
		color: var(--muted);
		font-size: 0.85rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 1.25rem 1rem;
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.grid.music-grid {
		grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
	}

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

	.muted {
		color: var(--muted);
	}
</style>
