/**
 * Start album, artist or single-track playback from anywhere in the app.
 *
 * Shelf cards only carry a small summary, so fetching the detail payload here
 * keeps the cards lightweight while still letting the play control queue the
 * whole album without leaving the page.
 */
import { api } from './api/client';
import type { AlbumDetail, ArtistDetail, S, TrackSummary } from './api/types';
import { addTracksToQueue, playQueue, type PlayerTrack } from './player.svelte';

function toPlayerTrack(
	track: TrackSummary | S['TrackDetail'],
	artworkUrl?: string | null
): PlayerTrack {
	return {
		id: track.id,
		title: track.title,
		artistName: track.artist_name ?? null,
		albumTitle: track.album_title ?? null,
		streamUrl: track.stream_url,
		artworkUrl,
		albumId: track.album_id ?? null,
		favorite: track.watch_state?.favorite ?? false,
		durationSeconds: track.duration_seconds,
		replayGainTrackGain: track.replaygain_track_gain,
		replayGainTrackPeak: track.replaygain_track_peak,
		replayGainAlbumGain: track.replaygain_album_gain,
		replayGainAlbumPeak: track.replaygain_album_peak
	};
}

/** Queue a whole album, optionally starting at one of its tracks. */
export async function playAlbum(
	albumId: string,
	artworkUrl?: string | null,
	startTrackId?: string | null
): Promise<void> {
	const album = await api.get<AlbumDetail>(`/albums/${albumId}`);
	const queue: PlayerTrack[] = album.discs.flatMap((disc) =>
		disc.tracks.map((track) => toPlayerTrack(track, artworkUrl))
	);
	const start = startTrackId ? queue.findIndex((track) => track.id === startTrackId) : -1;
	await playQueue(queue, Math.max(0, start));
}

/** Add every album track to the queue without starting playback. */
export async function queueAlbum(
	albumId: string,
	artworkUrl?: string | null,
	mode: 'end' | 'next' = 'end'
): Promise<void> {
	const album = await api.get<AlbumDetail>(`/albums/${albumId}`);
	const queue: PlayerTrack[] = album.discs.flatMap((disc) =>
		disc.tracks.map((track) => toPlayerTrack(track, artworkUrl))
	);
	addTracksToQueue(queue, mode);
}

/** Queue an artist's top tracks; each track resolves its own album cover. */
export async function playArtist(artistId: string): Promise<void> {
	const artist = await api.get<ArtistDetail>(`/artists/${artistId}`);
	const queue = artist.top_tracks.map((track) => toPlayerTrack(track));
	await playQueue(queue, 0);
}

/** Queue a single track (used when it has no album to expand into). */
export async function playTrack(trackId: string, artworkUrl?: string | null): Promise<void> {
	const track = await api.get<S['TrackDetail']>(`/tracks/${trackId}`);
	await playQueue([toPlayerTrack(track, artworkUrl)], 0);
}
