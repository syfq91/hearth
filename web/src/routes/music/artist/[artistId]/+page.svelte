<script lang="ts">
	import Poster from '../../../../lib/components/Poster.svelte';
	import { playQueue, player, type PlayerTrack } from '../../../../lib/player.svelte';
	import type { TrackSummary } from '../../../../lib/api/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	function toPlayerTrack(track: TrackSummary): PlayerTrack {
		return {
			id: track.id,
			title: track.title,
			artistName: track.artist_name ?? null,
			albumTitle: track.album_title ?? null,
			streamUrl: track.stream_url,
			artworkUrl: data.artist.poster?.url ?? null
		};
	}

	/** Top tracks play as their own queue, starting at the clicked row. */
	function playTracks(startIndex: number): void {
		const queue = data.artist.top_tracks.map(toPlayerTrack);
		if (queue.length > 0) void playQueue(queue, startIndex);
	}

	function formatDuration(seconds: number | null | undefined): string {
		if (!seconds || seconds <= 0) return '';
		const m = Math.floor(seconds / 60);
		const s = Math.floor(seconds % 60);
		return `${m}:${s.toString().padStart(2, '0')}`;
	}
</script>

<article class="artist">
	<div class="art">
		<Poster image={data.artist.poster} alt="" aspect="1 / 1" width={400} />
	</div>

	<div class="info">
		<nav class="crumbs" aria-label="Breadcrumb">
			<a href="/music">Music</a>
			<span aria-hidden="true">/</span>
			<a href="/music/artists">Artists</a>
		</nav>
		<h1>{data.artist.name}</h1>
		{#if data.artist.disambiguation}
			<p class="disambiguation">{data.artist.disambiguation}</p>
		{/if}
		<p class="meta">
			{[
				data.artist.country,
				data.artist.genres?.length ? data.artist.genres.join(', ') : null
			]
				.filter(Boolean)
				.join(' · ')}
		</p>
		{#if data.artist.overview}
			<p class="overview">{data.artist.overview}</p>
		{/if}
		{#if data.artist.top_tracks.length > 0}
			<div class="actions">
				<button type="button" class="play" onclick={() => playTracks(0)}>Play top tracks</button>
			</div>
		{/if}
	</div>
</article>

{#if data.artist.top_tracks.length > 0}
	<section aria-label="Top tracks">
		<h2>Top tracks</h2>
		<ol class="tracks">
			{#each data.artist.top_tracks as track, index (track.id)}
				<li class="track" class:current={player.track?.id === track.id}>
					<span class="number">{index + 1}</span>
					<button type="button" class="name" onclick={() => playTracks(index)}>
						{track.title}
					</button>
					<span class="album">
						{#if track.album_id}
							<a href={`/music/album/${track.album_id}`}>{track.album_title ?? 'Album'}</a>
						{:else}
							{track.album_title ?? ''}
						{/if}
					</span>
					<span class="duration">{formatDuration(track.duration_seconds)}</span>
				</li>
			{/each}
		</ol>
	</section>
{/if}

{#if data.artist.albums.length > 0}
	<section aria-label="Albums">
		<h2>Albums</h2>
		<ul class="grid">
			{#each data.artist.albums as album (album.id)}
				<li>
					<a href={`/music/album/${album.id}`} aria-label={album.title}>
						<Poster image={album.poster} alt="" aspect="1 / 1" width={300} />
					</a>
					<p class="album-title">
						<a href={`/music/album/${album.id}`}>{album.title}</a>
					</p>
					{#if album.year}
						<p class="album-year">{album.year}</p>
					{/if}
				</li>
			{/each}
		</ul>
	</section>
{/if}

<style>
	.artist {
		display: grid;
		grid-template-columns: minmax(180px, 260px) 1fr;
		gap: 2rem;
		align-items: start;
		margin-bottom: 2rem;
	}

	.art {
		border-radius: var(--radius);
		overflow: hidden;
	}

	.crumbs {
		font-size: 0.9rem;
		color: var(--muted);
		margin-bottom: 0.5rem;
	}

	.crumbs span {
		margin: 0 0.35rem;
	}

	.info h1 {
		font-size: 1.7rem;
		margin: 0 0 0.25rem;
	}

	.disambiguation {
		color: var(--muted);
		margin: 0 0 0.4rem;
	}

	.meta {
		color: var(--muted);
		font-size: 0.9rem;
		margin: 0 0 0.75rem;
	}

	.overview {
		max-width: 60ch;
		margin: 0 0 1rem;
		line-height: 1.55;
	}

	.actions {
		margin-top: 0.5rem;
	}

	.play {
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		border: 0;
		border-radius: var(--radius);
		padding: 0.6rem 1.5rem;
		cursor: pointer;
	}

	.play:hover {
		filter: brightness(1.05);
	}

	section h2 {
		font-size: 1.05rem;
		margin: 0 0 0.75rem;
	}

	section {
		margin-bottom: 2rem;
	}

	.tracks {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 0.15rem;
	}

	.track {
		display: grid;
		grid-template-columns: 2.5rem 1fr minmax(0, 18rem) auto;
		align-items: center;
		gap: 0.75rem;
		padding: 0.4rem 0.6rem;
		border-radius: 6px;
	}

	.track:hover {
		background: var(--surface);
	}

	.track.current .name {
		color: var(--accent);
	}

	.number {
		color: var(--muted);
		text-align: right;
		font-variant-numeric: tabular-nums;
	}

	.name {
		background: none;
		border: 0;
		color: inherit;
		text-align: left;
		padding: 0;
		cursor: pointer;
		font: inherit;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.name:hover {
		text-decoration: underline;
	}

	.album {
		color: var(--muted);
		font-size: 0.85rem;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.duration {
		color: var(--muted);
		font-size: 0.85rem;
		font-variant-numeric: tabular-nums;
	}

	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
		gap: 1.25rem 1rem;
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.album-title {
		font-size: 0.9rem;
		font-weight: 600;
		margin: 0.4rem 0 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.album-year {
		color: var(--muted);
		font-size: 0.8rem;
		margin: 0.15rem 0 0;
	}

	@media (max-width: 720px) {
		.artist {
			grid-template-columns: 1fr;
		}

		.art {
			max-width: 200px;
		}

		.track {
			grid-template-columns: 2rem 1fr auto;
		}

		.album {
			display: none;
		}
	}
</style>
