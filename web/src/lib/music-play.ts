/**
 * Start album playback from anywhere in the app.
 *
 * Album summaries on home shelves only carry a track count, so fetching the
 * detail payload here keeps the card lightweight while still letting the play
 * control queue the whole album without leaving the page.
 */
import { api } from './api/client';
import type { AlbumDetail } from './api/types';
import { playQueue, type PlayerTrack } from './player.svelte';

export async function playAlbum(albumId: string, artworkUrl?: string | null): Promise<void> {
	const album = await api.get<AlbumDetail>(`/albums/${albumId}`);
	const queue: PlayerTrack[] = album.discs.flatMap((disc) =>
		disc.tracks.map((track) => ({
			id: track.id,
			title: track.title,
			artistName: track.artist_name ?? null,
			albumTitle: track.album_title ?? null,
			streamUrl: track.stream_url,
			artworkUrl
		}))
	);
	await playQueue(queue, 0);
}
