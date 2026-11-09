import { api } from '../lib/api/client';
import type {
	AlbumSummary,
	BookShelf,
	HomeOverview,
	HomeShelf,
	LibraryShelf,
	LibrarySummary,
	NextUpShelf
} from '../lib/api/types';
import type { PageLoad } from './$types';

export interface LibraryRecentlyAdded {
	library: LibrarySummary;
	shelf: HomeShelf;
}

export interface HomeData {
	continueWatching: HomeShelf;
	continueReading: BookShelf;
	nextUp: NextUpShelf;
	recentLibraries: (LibraryRecentlyAdded | LibraryShelf)[];
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

	try {
		const overview = await api.get<HomeOverview>('/home', { query: { limit: 20 } });
		return {
			continueWatching: overview.continue_watching,
			continueReading: overview.continue_reading,
			nextUp: overview.next_up,
			recentLibraries: overview.recent_libraries,
			recentAlbums: overview.recent_albums,
			error: null
		};
	} catch (err) {
		const message = err instanceof Error ? err.message : String(err);
		return { ...empty, error: message };
	}
}

// Return the request promise instead of awaiting it: SvelteKit commits the
// navigation immediately and the page renders its own loading state.
export const load: PageLoad = () => {
	return { home: loadHome() };
};
