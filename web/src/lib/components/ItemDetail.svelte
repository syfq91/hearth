<script lang="ts">
	import Poster from './Poster.svelte';
	import { api } from '../api/client';
	import type { ItemDetail } from '../api/types';
	import { Play, RotateCcw, Heart, Check, Star } from '@lucide/svelte';

	interface Props {
		item: ItemDetail;
	}

	let { item }: Props = $props();

	// Local overrides layered on top of the server-provided watch state so a
	// toggle is instant; the next navigation refetches the authoritative value.
	let favoriteOverride = $state<boolean | null>(null);
	let watchedOverride = $state<boolean | null>(null);
	let favorite = $derived(favoriteOverride ?? item.watch_state?.favorite ?? false);
	let watched = $derived(watchedOverride ?? item.watch_state?.watched ?? false);
	let toggling = $state(false);

	async function setFavorite(): Promise<void> {
		toggling = true;
		try {
			await api.post(`/items/${item.id}/favorite`, { body: { favorite: !favorite } });
			favoriteOverride = !favorite;
		} finally {
			toggling = false;
		}
	}

	async function setWatched(): Promise<void> {
		toggling = true;
		try {
			await api.post(`/items/${item.id}/watched`, { body: { watched: !watched } });
			watchedOverride = !watched;
		} finally {
			toggling = false;
		}
	}

	const runtimeLabel = $derived.by(() => {
		const seconds = item.runtime_seconds ?? 0;
		if (seconds <= 0) return null;
		const minutes = Math.round(seconds / 60);
		return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
	});

	const meta = $derived(
		[
			item.year,
			runtimeLabel,
			item.content_rating
		].filter(Boolean)
	);

	const cast = $derived(
		(item.people ?? [])
			.filter((credit) => credit.role === 'actor')
			.sort((a, b) => a.ordering - b.ordering)
			.slice(0, 8)
	);
</script>

<article class="detail">
	<div class="art">
		<Poster image={item.poster} alt="" width={400} />
	</div>

	<div class="info">
		<h1>{item.title}</h1>
		{#if item.tagline}
			<p class="tagline">{item.tagline}</p>
		{/if}
		{#if meta.length > 0 || item.rating}
			<p class="meta">
				{#if meta.length > 0}
					<span>{meta.join(' · ')}</span>
				{/if}
				{#if item.rating}
					{#if meta.length > 0}<span class="dot">·</span>{/if}
					<span class="rating"><Star size={14} class="star-icon" fill="currentColor" /> {item.rating.toFixed(1)}</span>
				{/if}
			</p>
		{/if}

		<div class="actions">
			<a class="play" href={`/watch/${item.id}`}>
				{#if item.watch_state?.position_ms}
					<RotateCcw size={16} /> <span>Resume</span>
				{:else}
					<Play size={16} /> <span>Play</span>
				{/if}
			</a>
			<button type="button" class="ghost" onclick={setFavorite} disabled={toggling}>
				<Heart size={16} fill={favorite ? 'currentColor' : 'none'} color={favorite ? 'var(--accent)' : 'currentColor'} />
				<span>{favorite ? 'In favorites' : 'Favorite'}</span>
			</button>
			<button type="button" class="ghost" onclick={setWatched} disabled={toggling}>
				<Check size={16} />
				<span>{watched ? 'Watched' : 'Mark watched'}</span>
			</button>
		</div>

		{#if item.genres.length > 0}
			<p class="genres">{item.genres.join(' · ')}</p>
		{/if}

		{#if item.overview}
			<p class="overview">{item.overview}</p>
		{/if}

		{#if cast.length > 0}
			<section class="cast" aria-label="Cast">
				<h2>Cast</h2>
				<ul>
					{#each cast as credit (`${credit.name}:${credit.character}`)}
						<li><strong>{credit.name}</strong>{#if credit.character}<span class="muted"> as {credit.character}</span>{/if}</li>
					{/each}
				</ul>
			</section>
		{/if}
	</div>
</article>

<style>
	.detail {
		display: grid;
		grid-template-columns: minmax(200px, 280px) 1fr;
		gap: 2rem;
		align-items: start;
	}

	.art {
		border-radius: var(--radius);
		overflow: hidden;
	}

	.info h1 {
		font-size: 1.8rem;
	}

	.tagline {
		color: var(--muted);
		font-style: italic;
		margin: 0 0 0.5rem;
	}

	.meta {
		display: inline-flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.35rem;
		color: var(--muted);
		margin: 0 0 1rem;
	}

	.rating {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		color: #eab308;
		font-weight: 600;
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
		margin-bottom: 1.25rem;
	}

	.play {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		border-radius: var(--radius);
		padding: 0.6rem 1.6rem;
	}

	.play:hover {
		text-decoration: none;
		filter: brightness(1.05);
	}

	.ghost {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.6rem 1.1rem;
		cursor: pointer;
	}

	.ghost:hover:not(:disabled) {
		background: var(--surface);
	}

	.ghost:disabled {
		opacity: 0.6;
	}

	.genres {
		color: var(--muted);
		font-size: 0.9rem;
	}

	.overview {
		max-width: 60ch;
		margin: 1rem 0;
	}

	.cast h2 {
		font-size: 1rem;
		margin-bottom: 0.5rem;
	}

	.cast ul {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 0.25rem;
		font-size: 0.9rem;
	}

	.muted {
		color: var(--muted);
	}

	@media (max-width: 720px) {
		.detail {
			grid-template-columns: 1fr;
		}

		.art {
			max-width: 220px;
		}
	}
</style>
