import { api } from '../lib/api/client';
import type { AlbumSummary, BookShelf, HomeShelf, LibrarySummary, NextUpShelf } from '../lib/api/types';
import type { PageLoad } from './$types';

export interface LibraryRecentlyAdded {
	library: LibrarySummary;
	shelf: HomeShelf;
}

export interface HomeData {
	continueWatching: HomeShelf;
	continueReading: BookShelf;
	nextUp: NextUpShelf;
	recentLibraries: LibraryRecentlyAdded[];
	recentAlbums: AlbumSummary[];
	/** Shelf load failures, surfaced as a non-blocking banner. */
	error: string | null;
}

async function loadHome(): Promise<HomeData> {
	// In SPA mode (`ssr = false`) load functions run in the browser, so the
	// API client's global fetch (with bearer token + refresh) is correct here.
	const empty: HomeData = {
		continueWatching: { items: [], has_more: false },
		continueReading: { items: [], has_more: false },
		nextUp: { items: [] },
		recentLibraries: [],
		recentAlbums: [],
		error: null
	};
	const [continueWatching, continueReading, nextUp, recentAlbums, librariesResult] =
		await Promise.allSettled([
			api.get<HomeShelf>('/home/continue-watching', { query: { limit: 20 } }),
			api.get<BookShelf>('/home/continue-reading', { query: { limit: 20 } }),
			api.get<NextUpShelf>('/shows/next-up', { query: { limit: 20 } }),
			api.get<AlbumSummary[]>('/music/recently-added', { query: { limit: 20 } }),
			api.get<LibrarySummary[]>('/libraries')
		]);

	const libraries = librariesResult.status === 'fulfilled' ? librariesResult.value : [];
	const nonMusicLibraries = libraries.filter((lib) => lib.type !== 'music');
	const libraryShelves = await Promise.allSettled(
		nonMusicLibraries.map(async (lib) => {
			const shelf = await api.get<HomeShelf>('/home/recently-added', {
				query: { library_id: lib.id, limit: 20 }
			});
			return { library: lib, shelf };
		})
	);

	const recentLibraries: LibraryRecentlyAdded[] = [];
	for (const result of libraryShelves) {
		if (result.status === 'fulfilled' && result.value.shelf.items.length > 0) {
			recentLibraries.push(result.value);
		}
	}

	const data: HomeData = {
		continueWatching:
			continueWatching.status === 'fulfilled' ? continueWatching.value : empty.continueWatching,
		continueReading:
			continueReading.status === 'fulfilled' ? continueReading.value : empty.continueReading,
		nextUp: nextUp.status === 'fulfilled' ? nextUp.value : empty.nextUp,
		recentLibraries,
		recentAlbums: recentAlbums.status === 'fulfilled' ? recentAlbums.value : [],
		error: null
	};
	const failures = [
		continueWatching,
		continueReading,
		nextUp,
		recentAlbums,
		librariesResult,
		...libraryShelves
	].filter((result) => result.status === 'rejected');
	if (failures.length > 0) {
		const first = failures[0] as PromiseRejectedResult;
		data.error = first.reason instanceof Error ? first.reason.message : String(first.reason);
	}
	return data;
}

// Return the request promise instead of awaiting it: SvelteKit commits the
// navigation immediately and the page renders its own loading state.
export const load: PageLoad = () => {
	return { home: loadHome() };
};
