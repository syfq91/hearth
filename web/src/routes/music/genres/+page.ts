import { api } from '../../../lib/api/client';
import type { GenreListResponse } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ url }) => {
	const query = url.searchParams.get('q') ?? '';
	const genres = await api.get<GenreListResponse>('/genres', {
		query: { query: query || undefined, limit: 48 }
	});
	return { genres, query };
};
