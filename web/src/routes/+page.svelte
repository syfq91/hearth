<script lang="ts">
	import Shelf from '../lib/components/Shelf.svelte';
	import Poster from '../lib/components/Poster.svelte';
	import BookCover from '../lib/components/BookCover.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<svelte:head>
	<title>Home · Hearth</title>
</svelte:head>

<h1>Home</h1>

{#await data.home}
	<p class="muted" role="status">Loading…</p>
{:then home}
	{#if home.error}
		<p class="banner" role="status">Some shelves failed to load: {home.error}</p>
	{/if}

	<Shelf title="Continue watching" items={home.continueWatching.items} directPlay />

	{#if home.continueReading.items.length > 0}
		<section class="shelf" aria-label="Continue reading">
			<h2 class="heading">Continue reading</h2>
			<ul class="row">
				{#each home.continueReading.items as book (book.id)}
					<li>
						<article class="book">
							<a class="cover-link" href={`/books/${book.id}`} aria-label={book.title}>
								<BookCover coverImageId={book.cover_image_id} alt="" />
								{#if book.progress && book.progress.percentage > 0}
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
							{#if book.author}
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
		<section class="shelf" aria-label="Recently added albums">
			<h2 class="heading">Recently added albums</h2>
			<ul class="row">
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

	.heading {
		font-size: 1.1rem;
		margin-bottom: 0.75rem;
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

	.progress {
		height: 3px;
		background: var(--surface-2);
		border-radius: 2px;
		overflow: hidden;
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

	.cover-link {
		position: relative;
		display: block;
		border-radius: var(--radius);
		overflow: hidden;
	}

	.cover-link:hover {
		text-decoration: none;
		filter: brightness(1.08);
	}

	.cover-link .progress {
		position: absolute;
		left: 0;
		right: 0;
		bottom: 0;
		height: 4px;
		background: rgba(255, 255, 255, 0.2);
		border-radius: 0;
	}

	.cover-link .progress span {
		display: block;
		height: 100%;
		background: var(--accent);
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
		padding: 3rem 0;
	}

	.muted {
		color: var(--muted);
	}

	.button {
		display: inline-block;
		margin-top: 1rem;
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		border-radius: var(--radius);
		padding: 0.6rem 1.2rem;
	}

	.button:hover {
		text-decoration: none;
		filter: brightness(1.05);
	}
</style>
