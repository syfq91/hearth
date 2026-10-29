import { api } from '../../lib/api/client';
import type { AlbumListResponse } from '../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ url }) => {
	const query = url.searchParams.get('q') ?? '';
	const [recent, searched] = await Promise.all([
		api.get<AlbumListResponse>('/albums', { query: { sort: 'added', limit: 48 } }),
		query
			? api.get<AlbumListResponse>('/albums', { query: { query, limit: 48 } })
			: Promise.resolve(null)
	]);
	return { albums: searched ?? recent, query };
};
