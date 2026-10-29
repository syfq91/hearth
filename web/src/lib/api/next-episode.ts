/**
 * Next-episode lookup for the video player.
 *
 * The API exposes "next up" per series (which follows *watched* state), but
 * when an episode finishes the player needs the episode that follows in
 * series order regardless of what was just marked watched. That is derived
 * from the season/episode listings instead.
 */
import { api } from './client';
import type { EpisodeSummary, SeasonSummary } from './types';

export interface NextEpisode {
	id: string;
	/** `S2E5` style label, empty when numbers are missing. */
	label: string;
	title: string;
}

function label(season: number | null | undefined, episode: number | null | undefined): string {
	if (season == null || episode == null) return '';
	return `S${season}E${episode}`;
}

/** Find the episode that follows `episodeId` in series order. */
export async function findNextEpisode(episodeId: string): Promise<NextEpisode | null> {
	const episode = await api.get<EpisodeSummary>(`/episodes/${episodeId}`);
	if (!episode.series_id || !episode.season_id) return null;

	// 1. Next episode inside the same season.
	const siblings = await api.get<EpisodeSummary[]>(`/seasons/${episode.season_id}/episodes`);
	const index = siblings.findIndex((item) => item.id === episodeId);
	const next = index >= 0 ? siblings[index + 1] : undefined;
	if (next) {
		return { id: next.id, label: label(next.season_number, next.episode_number), title: next.title };
	}

	// 2. Last episode of the season: first episode of the next non-empty season.
	const seasons = await api.get<SeasonSummary[]>(`/series/${episode.series_id}/seasons`);
	const ordered = [...seasons].sort(
		(a, b) => (a.season_number ?? 0) - (b.season_number ?? 0)
	);
	const seasonIndex = ordered.findIndex((item) => item.id === episode.season_id);
	for (let i = seasonIndex + 1; i < ordered.length; i += 1) {
		const season = ordered[i];
		if ((season.episode_count ?? 0) === 0) continue;
		const episodes = await api.get<EpisodeSummary[]>(`/seasons/${season.id}/episodes`);
		const first = episodes[0];
		if (first) {
			return {
				id: first.id,
				label: label(first.season_number, first.episode_number),
				title: first.title
			};
		}
	}
	return null;
}
