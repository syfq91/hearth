<script lang="ts">
	import Poster from './Poster.svelte';
	import { playAlbum, playArtist, playTrack } from '../music-play';
	import type { S } from '../api/types';

	interface Props {
		item: S['ItemSummary'];
		/** Heading level for the card title. */
		level?: 2 | 3 | 4;
	}

	let { item, level = 3 }: Props = $props();

	const isAlbum = $derived(item.type === 'album' || item.type === 'music');
	const isArtist = $derived(item.type === 'artist');
	const isTrack = $derived(item.type === 'track');

	const subtitle = $derived(isArtist ? 'Artist' : isTrack ? 'Song' : 'Album');

	// Tracks link to their parent album: a track id is not a valid album id.
	const detailTarget = $derived.by(() => {
		if (isArtist) return `/music/artist/${item.id}`;
		if (isTrack && item.album_id) return `/music/album/${item.album_id}`;
		return `/music/album/${item.id}`;
	});

	let playing = $state(false);
	let playError = $state<string | null>(null);

	async function play(): Promise<void> {
		if (playing) return;
		playing = true;
		playError = null;
		try {
			if (isAlbum) {
				await playAlbum(item.id, item.poster?.url);
			} else if (isArtist) {
				await playArtist(item.id);
			} else if (item.album_id) {
				// Queue the album but start at the favourited song.
				await playAlbum(item.album_id, item.poster?.url, item.id);
			} else {
				await playTrack(item.id, item.poster?.url);
			}
		} catch (error) {
			playError = error instanceof Error ? error.message : String(error);
		} finally {
			playing = false;
		}
	}
</script>

<article class="card">
	<button
		type="button"
		class="art"
		onclick={() => void play()}
		aria-label={`Play ${item.title}`}
		disabled={playing}
	>
		<Poster image={item.poster} alt="" aspect="1 / 1" width={300} />
	</button>
	<svelte:element this={`h${level}`} class="title">
		<a class="text-link" href={detailTarget}>{item.title}</a>
	</svelte:element>
	{#if subtitle}
		<p class="subtitle">{subtitle}</p>
	{/if}
	{#if playError}
		<p class="error" role="alert">{playError}</p>
	{/if}
</article>

<style>
	.card {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		width: 100%;
	}

	.art {
		display: block;
		width: 100%;
		padding: 0;
		border: 0;
		background: none;
		border-radius: var(--radius);
		overflow: hidden;
		cursor: pointer;
	}

	.art:hover {
		filter: brightness(1.08);
	}

	.art:focus-visible {
		outline: 2px solid var(--accent);
		outline-offset: 2px;
	}

	.art:disabled {
		cursor: progress;
	}

	.text-link {
		color: inherit;
		text-decoration: none;
		display: block;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.text-link:hover {
		color: var(--accent);
		text-decoration: underline;
	}

	.text-link:focus-visible {
		outline: 2px solid var(--accent);
		outline-offset: 2px;
		border-radius: 2px;
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

	.error {
		color: var(--danger);
		font-size: 0.8rem;
		margin: 0;
	}
</style>
