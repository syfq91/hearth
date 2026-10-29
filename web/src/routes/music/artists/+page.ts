import { api } from '../../../lib/api/client';
import type { ArtistListResponse } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ url }) => {
	const query = url.searchParams.get('q') ?? '';
	const artists = await api.get<ArtistListResponse>('/artists', {
		query: { query: query || undefined, limit: 48 }
	});
	return { artists, query };
};
