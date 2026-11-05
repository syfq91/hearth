import { api } from '../../lib/api/client';
import type { AlbumListResponse } from '../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ url }) => {
	const query = url.searchParams.get('q') ?? '';
	const genre = url.searchParams.get('genre') ?? '';
	const [recent, filtered] = await Promise.all([
		api.get<AlbumListResponse>('/albums', { query: { sort: 'added', limit: 48 } }),
		query || genre
			? api.get<AlbumListResponse>('/albums', {
					query: { query: query || undefined, genre: genre || undefined, limit: 48 }
				})
			: Promise.resolve(null)
	]);
	return { albums: filtered ?? recent, query, genre };
};
