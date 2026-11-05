import { api } from '../../lib/api/client';
import type { AlbumListResponse } from '../../lib/api/types';
import type { PageLoad } from './$types';

// Return the request promise instead of awaiting it: SvelteKit commits the
// navigation immediately and the page renders its own loading state.
export const load: PageLoad = ({ url }) => {
	const query = url.searchParams.get('q') ?? '';
	const genre = url.searchParams.get('genre') ?? '';
	const albums =
		query || genre
			? api.get<AlbumListResponse>('/albums', {
					query: { query: query || undefined, genre: genre || undefined, limit: 48 }
				})
			: api.get<AlbumListResponse>('/albums', { query: { sort: 'added', limit: 48 } });
	return { albums, query, genre };
};
