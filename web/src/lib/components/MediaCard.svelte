<script lang="ts">
	import Poster from './Poster.svelte';
	import type { S } from '../api/types';
	import { Check } from '@lucide/svelte';

	interface Props {
		item: S['ItemSummary'] | S['NextUpEpisode'];
		/** Heading level for the card title. */
		level?: 2 | 3 | 4;
		/** When true, video cards navigate directly to playback. */
		directPlay?: boolean;
	}

	let { item, level = 3, directPlay = false }: Props = $props();

	// An item is an episode if it's typed 'episode' or has NextUp's series_title
	const isEpisode = $derived(
		('type' in item && item.type === 'episode') ||
			('series_title' in item && typeof item.series_title === 'string' && item.series_title.length > 0)
	);

	const episodeCount = $derived('episode_count' in item ? item.episode_count : null);

	const seriesTitle = $derived.by(() => {
		if ('series_title' in item && item.series_title) return item.series_title;
		return null;
	});

	const seriesId = $derived.by(() => {
		if ('series_id' in item && item.series_id) return item.series_id;
		return null;
	});

	const seasonNumber = $derived('season_number' in item ? item.season_number : null);
	const episodeNumber = $derived('episode_number' in item ? item.episode_number : null);

	const episodeLabel = $derived.by(() => {
		let num = '';
		if (seasonNumber != null && episodeNumber != null) {
			num = `S${seasonNumber}E${episodeNumber}`;
		} else if (episodeNumber != null) {
			num = `Episode ${episodeNumber}`;
		}
		const epTitle = (item.title ?? '').trim();
		if (
			num &&
			epTitle &&
			epTitle.toLowerCase() !== num.toLowerCase() &&
			epTitle.toLowerCase() !== `episode ${episodeNumber}`
		) {
			return `${num} · ${epTitle}`;
		}
		return num || epTitle || 'Episode';
	});

	const poster = $derived.by(() => {
		if ('series_poster' in item && item.series_poster) return item.series_poster;
		if (!isEpisode) return (item as S['ItemSummary']).poster;
		if ('still' in item && item.still) return item.still;
		return (item as S['ItemSummary']).poster;
	});

	const aspect = $derived(
		poster?.kind === 'still' || poster?.kind === 'thumbnail' ? '16 / 9' : '2 / 3'
	);

	const posterHref = $derived.by(() => {
		if (isEpisode) return `/watch/${item.id}`;
		const card = item as S['ItemSummary'];
		if (directPlay && (card.type === 'movie' || card.type === 'episode' || card.type === 'series')) {
			return `/watch/${card.id}`;
		}
		return detailHref(card);
	});

	function extractVolume(title: string): string | null {
		const match = title.match(/\b(?:vol(?:ume)?\.?|v)\s*(\d+(?:\.\d+)?)\b/i);
		if (match) {
			const num = match[1].replace(/^0+/, '') || match[1];
			return `Vol. ${num}`;
		}
		return null;
	}

	const subtitle = $derived.by(() => {
		if (episodeCount != null && episodeCount > 1) {
			return `${episodeCount} new episodes`;
		}
		if (isEpisode) return null;
		const card = item as S['ItemSummary'];

		if (card.type === 'book') {
			// First row for the book title.
			// Second row for the volume if available, year if volume not available, empty if all not available.
			const vol = ('volume' in card && card.volume ? String(card.volume) : null) || extractVolume(card.title ?? '');
			if (vol) return vol;
			if (card.year) return String(card.year);
			return null;
		}

		// Remove "movie", "episode", and "series" label on the card
		const typeLabel =
			card.type === 'movie' || card.type === 'episode' || card.type === 'series'
				? null
				: card.type;
		return [card.year, typeLabel].filter(Boolean).join(' · ');
	});

	const position = $derived(item.watch_state?.position_ms ?? 0);
	const runtimeMs = $derived((item.runtime_seconds ?? 0) * 1000);
	const percent = $derived(runtimeMs > 0 ? Math.min(100, (position / runtimeMs) * 100) : 0);
	const watched = $derived(item.watch_state?.watched ?? false);

	function detailHref(card: S['ItemSummary']): string {
		switch (card.type) {
			case 'series':
				return `/series/${card.id}`;
			case 'episode':
				return `/episodes/${card.id}`;
			case 'music':
			case 'album':
			case 'track':
				return `/music/album/${card.id}`;
			case 'book':
				return `/books/${card.id}`;
			default:
				return `/movies/${card.id}`;
		}
	}

	const libraryId = $derived.by(() => {
		if ('library_id' in item && typeof item.library_id === 'string' && item.library_id) {
			return item.library_id;
		}
		return null;
	});

	function withLib(url: string): string {
		if (!libraryId) return url;
		const sep = url.includes('?') ? '&' : '?';
		return `${url}${sep}lib=${encodeURIComponent(libraryId)}`;
	}
</script>

<article class="card">
	<a
		class="link"
		href={withLib(posterHref)}
		aria-label={isEpisode ? (seriesTitle ? `Play ${seriesTitle}` : `Play ${item.title}`) : item.title}
	>
		<Poster image={poster} alt="" {aspect} />
		{#if watched}
			<div class="badge checkmark-badge" aria-label="Completed">
				<Check size={13} strokeWidth={2.8} />
			</div>
		{:else if episodeCount != null && episodeCount > 1}
			<div class="badge" aria-label="{episodeCount} new episodes">
				{episodeCount}
			</div>
		{/if}
		{#if percent > 0 && !watched}
			<div class="progress" role="presentation">
				<span style:width="{percent}%"></span>
			</div>
		{/if}
	</a>
	{#if isEpisode}
		<svelte:element this={`h${level}`} class="title">
			{#if seriesId}
				<a class="text-link" href={withLib(`/series/${seriesId}`)}>{seriesTitle ?? item.title}</a>
			{:else}
				<a class="text-link" href={withLib(`/episodes/${item.id}`)}>{seriesTitle ?? item.title}</a>
			{/if}
		</svelte:element>
		<p class="subtitle">
			<a class="text-link" href={withLib(`/episodes/${item.id}`)}>{episodeLabel}</a>
		</p>
	{:else}
		<svelte:element this={`h${level}`} class="title">
			<a class="text-link" href={withLib(detailHref(item as S['ItemSummary']))}>{item.title}</a>
		</svelte:element>
		{#if subtitle}
			<p class="subtitle">{subtitle}</p>
		{/if}
	{/if}
</article>

<style>
	.card {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		width: 100%;
	}

	.link {
		position: relative;
		display: block;
		border-radius: var(--radius);
		overflow: hidden;
	}

	.link:hover {
		text-decoration: none;
		filter: brightness(1.08);
	}

	.badge {
		position: absolute;
		top: 0.4rem;
		right: 0.4rem;
		background: var(--accent);
		color: var(--accent-contrast);
		font-size: 0.75rem;
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
		width: 1.35rem;
		height: 1.35rem;
		padding: 0;
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
</style>
