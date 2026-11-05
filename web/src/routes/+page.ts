import { api } from '../lib/api/client';
import type { AlbumSummary, BookShelf, HomeShelf, NextUpShelf } from '../lib/api/types';
import type { PageLoad } from './$types';

export interface HomeData {
	continueWatching: HomeShelf;
	continueReading: BookShelf;
	nextUp: NextUpShelf;
	recentlyAdded: HomeShelf;
	favorites: HomeShelf;
	recentAlbums: AlbumSummary[];
	/** Shelf load failures, surfaced as a non-blocking banner. */
	error: string | null;
}

export const load: PageLoad = async () => {
	// In SPA mode (`ssr = false`) load functions run in the browser, so the
	// API client's global fetch (with bearer token + refresh) is correct here.
	const empty: HomeData = {
		continueWatching: { items: [], has_more: false },
		continueReading: { items: [], has_more: false },
		nextUp: { items: [] },
		recentlyAdded: { items: [], has_more: false },
		favorites: { items: [], has_more: false },
		recentAlbums: [],
		error: null
	};
	const [continueWatching, continueReading, nextUp, recentlyAdded, favorites, recentAlbums] =
		await Promise.allSettled([
			api.get<HomeShelf>('/home/continue-watching', { query: { limit: 20 } }),
			api.get<BookShelf>('/home/continue-reading', { query: { limit: 20 } }),
			api.get<NextUpShelf>('/shows/next-up', { query: { limit: 20 } }),
			api.get<HomeShelf>('/home/recently-added', { query: { limit: 20 } }),
			api.get<HomeShelf>('/home/favorites', { query: { limit: 20 } }),
			api.get<AlbumSummary[]>('/music/recently-added', { query: { limit: 20 } })
		]);
	const data: HomeData = {
		continueWatching:
			continueWatching.status === 'fulfilled' ? continueWatching.value : empty.continueWatching,
		continueReading:
			continueReading.status === 'fulfilled' ? continueReading.value : empty.continueReading,
		nextUp: nextUp.status === 'fulfilled' ? nextUp.value : empty.nextUp,
		recentlyAdded: recentlyAdded.status === 'fulfilled' ? recentlyAdded.value : empty.recentlyAdded,
		favorites: favorites.status === 'fulfilled' ? favorites.value : empty.favorites,
		recentAlbums: recentAlbums.status === 'fulfilled' ? recentAlbums.value : [],
		error: null
	};
	const failures = [
		continueWatching,
		continueReading,
		nextUp,
		recentlyAdded,
		favorites,
		recentAlbums
	].filter((result) => result.status === 'rejected');
	if (failures.length > 0) {
		const first = failures[0] as PromiseRejectedResult;
		data.error = first.reason instanceof Error ? first.reason.message : String(first.reason);
	}
	return data;
};
