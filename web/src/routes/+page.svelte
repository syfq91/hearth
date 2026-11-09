<script lang="ts">
	import Shelf from '../lib/components/Shelf.svelte';
	import Poster from '../lib/components/Poster.svelte';
	import BookCover from '../lib/components/BookCover.svelte';
	import { Check, ChevronLeft, ChevronRight } from '@lucide/svelte';
	import type { PageProps } from './$types';
	import type { BookShelf } from '../lib/api/types';

	let { data }: PageProps = $props();

	const musicLibrary = $derived(data.libraries?.find((l) => l.type === 'music'));
	const musicTitle = $derived(musicLibrary ? `New in ${musicLibrary.name}` : 'New in Music');
	const musicHref = $derived(musicLibrary ? `/library/${musicLibrary.id}` : '/music');

	let readingRowEl = $state<HTMLElement | null>(null);
	let canReadingScrollLeft = $state(false);
	let canReadingScrollRight = $state(false);
	let readingHasOverflow = $state(false);

	let albumRowEl = $state<HTMLElement | null>(null);
	let canAlbumScrollLeft = $state(false);
	let canAlbumScrollRight = $state(false);
	let albumHasOverflow = $state(false);

	function updateReadingScroll() {
		if (!readingRowEl) return;
		const { scrollLeft, scrollWidth, clientWidth } = readingRowEl;
		readingHasOverflow = scrollWidth > clientWidth + 4;
		canReadingScrollLeft = scrollLeft > 4;
		canReadingScrollRight = scrollLeft + clientWidth < scrollWidth - 4;
	}

	function updateAlbumScroll() {
		if (!albumRowEl) return;
		const { scrollLeft, scrollWidth, clientWidth } = albumRowEl;
		albumHasOverflow = scrollWidth > clientWidth + 4;
		canAlbumScrollLeft = scrollLeft > 4;
		canAlbumScrollRight = scrollLeft + clientWidth < scrollWidth - 4;
	}

	function scrollRow(el: HTMLElement | null, direction: 'left' | 'right') {
		if (!el) return;
		const distance = el.clientWidth * 0.75;
		el.scrollBy({
			left: direction === 'left' ? -distance : distance,
			behavior: 'smooth'
		});
	}

	function getBookVolume(book: BookShelf['items'][number]): string | null {
		if (book.series?.index != null) {
			const idx = book.series.index;
			return `Vol. ${idx}`;
		}
		const match = book.title.match(/\b(?:vol(?:ume)?\.?|v)\s*(\d+(?:\.\d+)?)\b/i);
		if (match) {
			const num = match[1].replace(/^0+/, '') || match[1];
			return `Vol. ${num}`;
		}
		return null;
	}

	$effect(() => {
		if (readingRowEl) {
			const el = readingRowEl;
			updateReadingScroll();
			el.addEventListener('scroll', updateReadingScroll, { passive: true });
			const ro = new ResizeObserver(updateReadingScroll);
			ro.observe(el);
			return () => {
				el.removeEventListener('scroll', updateReadingScroll);
				ro.disconnect();
			};
		}
	});

	$effect(() => {
		if (albumRowEl) {
			const el = albumRowEl;
			updateAlbumScroll();
			el.addEventListener('scroll', updateAlbumScroll, { passive: true });
			const ro = new ResizeObserver(updateAlbumScroll);
			ro.observe(el);
			return () => {
				el.removeEventListener('scroll', updateAlbumScroll);
				ro.disconnect();
			};
		}
	});
</script>

<svelte:head>
	<title>Home · Hearth</title>
</svelte:head>

<h1 class="visually-hidden">Home</h1>

{#await data.home}
	<p class="muted" role="status">Loading…</p>
{:then home}
	{#if home.error}
		<p class="banner" role="status">Some shelves failed to load: {home.error}</p>
	{/if}

	<Shelf title="Continue watching" items={home.continueWatching.items} directPlay />

	{#if home.continueReading.items.length > 0}
		<section class="shelf" aria-label="Continue reading">
			<div class="shelf-header">
				<h2 class="heading">Continue reading</h2>
				{#if readingHasOverflow}
					<div class="scroll-arrows">
						<button
							type="button"
							class="scroll-btn"
							onclick={() => scrollRow(readingRowEl, 'left')}
							disabled={!canReadingScrollLeft}
							aria-label="Scroll Continue reading left"
							title="Scroll left"
						>
							<ChevronLeft size={18} />
						</button>
						<button
							type="button"
							class="scroll-btn"
							onclick={() => scrollRow(readingRowEl, 'right')}
							disabled={!canReadingScrollRight}
							aria-label="Scroll Continue reading right"
							title="Scroll right"
						>
							<ChevronRight size={18} />
						</button>
					</div>
				{/if}
			</div>
			<ul class="row" bind:this={readingRowEl}>
				{#each home.continueReading.items as book (book.id)}
					{@const volume = getBookVolume(book)}
					<li>
						<article class="book">
							<a class="cover-link" href={`/books/${book.id}`} aria-label={book.title}>
								<BookCover coverImageId={book.cover_image_id} alt="" />
								{#if book.progress?.completed}
									<div class="badge checkmark-badge" aria-label="Completed">
										<Check size={13} strokeWidth={2.8} />
									</div>
								{:else if book.progress && book.progress.percentage > 0}
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
							</a>
							<h3 class="title">{book.title}</h3>
							{#if volume}
								<p class="subtitle">{volume}</p>
							{:else if book.author}
								<p class="subtitle">{book.author.name}</p>
							{/if}
						</article>
					</li>
				{/each}
			</ul>
		</section>
	{/if}

	<Shelf title="Next up" items={home.nextUp.items} />

	{#if home.recentAlbums.length > 0}
		<section class="shelf" aria-label={musicTitle}>
			<div class="shelf-header">
				<h2 class="heading">
					<a class="heading-link" href={musicHref}>{musicTitle}</a>
				</h2>
				{#if albumHasOverflow}
					<div class="scroll-arrows">
						<button
							type="button"
							class="scroll-btn"
							onclick={() => scrollRow(albumRowEl, 'left')}
							disabled={!canAlbumScrollLeft}
							aria-label={`Scroll ${musicTitle} left`}
							title="Scroll left"
						>
							<ChevronLeft size={18} />
						</button>
						<button
							type="button"
							class="scroll-btn"
							onclick={() => scrollRow(albumRowEl, 'right')}
							disabled={!canAlbumScrollRight}
							aria-label={`Scroll ${musicTitle} right`}
							title="Scroll right"
						>
							<ChevronRight size={18} />
						</button>
					</div>
				{/if}
			</div>
			<ul class="row" bind:this={albumRowEl}>
				{#each home.recentAlbums as album (album.id)}
					<li>
						<article class="album">
							<a href={`/music/album/${album.id}`} aria-label={album.title}>
								<Poster image={album.poster} alt="" aspect="1 / 1" width={300} />
							</a>
							<h3 class="title">{album.title}</h3>
							{#if album.artist_name}
								<p class="subtitle">{album.artist_name}</p>
							{/if}
						</article>
					</li>
				{/each}
			</ul>
		</section>
	{/if}

	{#each home.recentLibraries as entry (entry.library.id)}
		<Shelf
			title={`New in ${entry.library.name}`}
			items={entry.shelf.items}
			href={`/library/${entry.library.id}`}
		/>
	{/each}

	{#if home.recentAlbums.length === 0 && home.continueWatching.items.length === 0 && home.continueReading.items.length === 0 && home.nextUp.items.length === 0 && home.recentLibraries.length === 0 && !home.error}
		<div class="empty-state">
			<p>Nothing here yet.</p>
			<p class="muted">Add a library from the admin area to get started.</p>
			<a class="button" href="/admin/libraries">Set up libraries</a>
		</div>
	{/if}
{:catch error}
	<p class="banner" role="alert">{error instanceof Error ? error.message : String(error)}</p>
{/await}

<style>
	.banner {
		background: var(--surface);
		border: 1px solid var(--border);
		border-left: 3px solid var(--danger);
		border-radius: var(--radius);
		padding: 0.6rem 1rem;
		color: var(--muted);
	}

	.shelf {
		margin-bottom: 2rem;
	}

	.shelf-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		margin-bottom: 0.75rem;
		gap: 0.5rem;
	}

	.heading {
		font-size: 1.1rem;
		margin: 0;
	}

	.heading-link {
		color: inherit;
		text-decoration: none;
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}

	.heading-link:hover {
		color: var(--accent);
		text-decoration: underline;
	}

	.scroll-arrows {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}

	.scroll-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.85rem;
		height: 1.85rem;
		padding: 0;
		border-radius: var(--radius);
		background: var(--surface-2);
		border: 1px solid var(--border);
		color: var(--text);
		cursor: pointer;
		transition:
			background 0.15s ease,
			opacity 0.15s ease,
			color 0.15s ease,
			border-color 0.15s ease;
	}

	.scroll-btn:hover:not(:disabled) {
		background: var(--accent);
		color: var(--accent-contrast);
		border-color: var(--accent);
	}

	.scroll-btn:disabled {
		opacity: 0.3;
		cursor: default;
	}

	.row {
		display: grid;
		grid-auto-flow: column;
		grid-auto-columns: clamp(140px, 18vw, 200px);
		gap: 1rem;
		overflow-x: auto;
		padding: 0.25rem 0.25rem 0.75rem;
		margin: 0;
		list-style: none;
		scroll-snap-type: x proximity;
	}

	.album,
	.book {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		width: 100%;
	}

	.cover-link {
		position: relative;
		display: block;
		border-radius: var(--radius);
		overflow: hidden;
	}

	.badge {
		position: absolute;
		top: 0.4rem;
		right: 0.4rem;
		background: var(--surface-2);
		color: var(--text);
		border: 1px solid var(--border);
		font-size: 0.72rem;
		font-weight: 700;
		min-width: 1.35rem;
		height: 1.35rem;
		line-height: 1.35rem;
		padding: 0 0.35rem;
		border-radius: 999px;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		box-shadow: 0 2px 6px rgba(0, 0, 0, 0.4);
		z-index: 2;
	}

	.checkmark-badge {
		background: #16a34a;
		color: #ffffff;
		border-color: #16a34a;
		width: 1.35rem;
		height: 1.35rem;
		padding: 0;
	}

	.progress {
		position: absolute;
		left: 0;
		right: 0;
		bottom: 0;
		height: 4px;
		background: rgba(255, 255, 255, 0.2);
	}

	.progress span {
		display: block;
		height: 100%;
		background: var(--accent);
	}

	.title {
		font-size: 0.95rem;
		font-weight: 600;
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.subtitle {
		font-size: 0.8rem;
		color: var(--muted);
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.empty-state {
		text-align: center;
		padding: 3rem 1rem;
		color: var(--muted);
	}

	.button {
		display: inline-block;
		margin-top: 1rem;
		padding: 0.5rem 1rem;
		background: var(--accent);
		color: var(--accent-contrast);
		border-radius: var(--radius);
		text-decoration: none;
		font-weight: 500;
	}
</style>
