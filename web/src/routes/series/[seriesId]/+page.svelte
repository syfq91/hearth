<script lang="ts">
	import Poster from '../../../lib/components/Poster.svelte';
	import { api } from '../../../lib/api/client';
	import type { EpisodeDetail } from '../../../lib/api/types';
	import type { PageProps } from './$types';
	import { Star, Play, Check } from '@lucide/svelte';

	let { data }: PageProps = $props();

	// SvelteKit passes the load result as `data`; keep the template reading
	// `series` while staying reactive across param changes.
	const series = $derived(data.series);

	let selectedSeason = $state<string | null>(null);
	let episodes = $state<EpisodeDetail[]>([]);
	let loading = $state(false);
	let error = $state<string | null>(null);

	// Default to the first season, and re-default if the series changes or
	// the selected season no longer belongs to it.
	$effect(() => {
		const seasons = series.seasons;
		if (selectedSeason === null || !seasons.some((season) => season.id === selectedSeason)) {
			selectedSeason = seasons[0]?.id ?? null;
		}
	});

	$effect(() => {
		const seasonId = selectedSeason;
		if (!seasonId) return;
		let cancelled = false;
		loading = true;
		error = null;
		void api
			.get<EpisodeDetail[]>(`/seasons/${seasonId}/episodes`)
			.then((list) => {
				if (!cancelled) episodes = list;
			})
			.catch((e: unknown) => {
				if (!cancelled) error = e instanceof Error ? e.message : String(e);
			})
			.finally(() => {
				if (!cancelled) loading = false;
			});
		return () => {
			cancelled = true;
		};
	});

	function runtimeLabel(seconds: number | null | undefined): string | null {
		if (!seconds || seconds <= 0) return null;
		const minutes = Math.round(seconds / 60);
		return `${minutes} min`;
	}
</script>

<svelte:head>
	<title>{series.title} · Hearth</title>
</svelte:head>

<article class="series">
	<header class="head">
		<div class="art">
			<Poster image={series.poster} alt="" width={400} />
		</div>
		<div class="info">
			<h1>{series.title}</h1>
			<p class="meta">
				{#each [
					series.year,
					series.runtime_seconds ? `${Math.round(series.runtime_seconds / 60)} min/ep` : null,
					series.total_seasons ? `${series.total_seasons} seasons` : null
				].filter(Boolean) as item, i}
					{#if i > 0}<span class="dot">·</span>{/if}
					<span>{item}</span>
				{/each}
				{#if series.rating}
					<span class="dot">·</span>
					<span class="rating"><Star size={14} class="star-icon" fill="currentColor" /> {series.rating.toFixed(1)}</span>
				{/if}
			</p>
			<div class="actions">
				<a class="play" href={`/watch/${series.id}`}>
					<Play size={16} /> <span>Play</span>
				</a>
			</div>
			{#if series.genres.length > 0}
				<p class="genres">{series.genres.join(' · ')}</p>
			{/if}
			{#if series.overview}
				<p class="overview">{series.overview}</p>
			{/if}
		</div>
	</header>

	<section class="episodes" aria-label="Episodes">
		<h2>Episodes</h2>
		{#if series.seasons.length === 0}
			<p class="muted">No seasons yet.</p>
		{:else}
			<label class="season-picker">
				<span>Season</span>
				<select bind:value={selectedSeason}>
					{#each series.seasons as season (season.id)}
						<option value={season.id}>
							{season.title ?? `Season ${season.season_number}`}
						</option>
					{/each}
				</select>
			</label>

			{#if error}
				<p class="banner" role="alert">{error}</p>
			{:else if loading}
				<p class="muted" role="status">Loading episodes…</p>
			{:else if episodes.length === 0}
				<p class="muted">No episodes in this season.</p>
			{:else}
				<ol class="episode-list">
					{#each episodes as episode (episode.id)}
						<li class="episode">
							<a class="still" href={`/watch/${episode.id}`} aria-label={`Play ${episode.title}`}>
								<Poster image={episode.still} alt="" aspect="16 / 9" width={320} />
							</a>
							<div class="episode-info">
								<h3 class="episode-title">
									<a href={`/watch/${episode.id}`}>
										{episode.episode_number}. {episode.title}
									</a>
								</h3>
								<p class="episode-meta">
									{[runtimeLabel(episode.runtime_seconds), episode.air_date]
										.filter(Boolean)
										.join(' · ')}
									{#if episode.watch_state?.watched}
										<span class="watched"><Check size={13} /> Watched</span>
									{/if}
								</p>
								{#if episode.overview}
									<p class="episode-overview">{episode.overview}</p>
								{/if}
							</div>
						</li>
					{/each}
				</ol>
			{/if}
		{/if}
	</section>
</article>

<style>
	.head {
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

	.info h1 {
		font-size: 1.8rem;
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
		margin-bottom: 1rem;
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

	.genres {
		color: var(--muted);
		font-size: 0.9rem;
	}

	.overview {
		max-width: 60ch;
		margin: 1rem 0 0;
	}

	.episodes h2 {
		font-size: 1.1rem;
		margin-bottom: 0.75rem;
	}

	.season-picker {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-bottom: 1rem;
		color: var(--muted);
		font-size: 0.9rem;
	}

	.season-picker select {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.35rem 0.5rem;
	}

	.episode-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 1.25rem;
	}

	.episode {
		display: grid;
		grid-template-columns: minmax(160px, 260px) 1fr;
		gap: 1rem;
	}

	.still {
		display: block;
		border-radius: var(--radius);
		overflow: hidden;
	}

	.episode-title {
		font-size: 1rem;
		margin: 0 0 0.25rem;
	}

	.episode-meta {
		color: var(--muted);
		font-size: 0.85rem;
		margin: 0 0 0.5rem;
	}

	.watched {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
		color: var(--accent);
		margin-left: 0.5rem;
	}

	.episode-overview {
		font-size: 0.9rem;
		color: var(--muted);
		margin: 0;
		max-width: 70ch;
	}

	.banner {
		background: var(--surface);
		border: 1px solid var(--border);
		border-left: 3px solid var(--danger);
		border-radius: var(--radius);
		padding: 0.6rem 1rem;
		color: var(--muted);
	}

	.muted {
		color: var(--muted);
	}

	@media (max-width: 720px) {
		.head,
		.episode {
			grid-template-columns: 1fr;
		}

		.art {
			max-width: 200px;
		}

		.still {
			max-width: 260px;
		}
	}
</style>
