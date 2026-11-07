<script lang="ts">
	import Poster from './Poster.svelte';
	import type { S } from '../api/types';

	interface Props {
		item: S['ItemSummary'] | S['NextUpEpisode'];
		/** Heading level for the card title. */
		level?: 2 | 3 | 4;
		/** When true, video cards navigate directly to playback. */
		directPlay?: boolean;
	}

	let { item, level = 3, directPlay = false }: Props = $props();

	// Next Up episodes carry `series_title`; catalog cards carry `type`.
	const isEpisode = $derived('series_title' in item);
	const poster = $derived.by(() => {
		if (!isEpisode) return (item as S['ItemSummary']).poster;
		const episode = item as S['NextUpEpisode'];
		return episode.still ?? episode.series_poster;
	});
	const href = $derived.by(() => {
		if (isEpisode) return `/watch/${item.id}`;
		const card = item as S['ItemSummary'];
		if (directPlay && (card.type === 'movie' || card.type === 'episode' || card.type === 'series')) {
			return `/watch/${card.id}`;
		}
		return detailHref(card);
	});
	const subtitle = $derived.by(() => {
		if (isEpisode) {
			const episode = item as S['NextUpEpisode'];
			return `${episode.series_title} · S${episode.season_number ?? '?'}E${episode.episode_number ?? '?'}`;
		}
		const card = item as S['ItemSummary'];
		return [card.year, card.type].filter(Boolean).join(' · ');
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
				return `/watch/${card.id}`;
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
</script>

<article class="card">
	<a class="link" href={href} aria-label={item.title}>
		<Poster image={poster} alt="" aspect={isEpisode ? '16 / 9' : '2 / 3'} />
		{#if percent > 0 && !watched}
			<div class="progress" role="presentation">
				<span style:width="{percent}%"></span>
			</div>
		{/if}
	</a>
	<svelte:element this={`h${level}`} class="title">{item.title}</svelte:element>
	{#if subtitle}
		<p class="subtitle">{subtitle}</p>
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
