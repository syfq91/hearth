import { api } from '../../lib/api/client';
import type { SearchResponse } from '../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ url }) => {
	const q = url.searchParams.get('q') ?? '';
	const type = url.searchParams.get('type') ?? 'all';
	if (!q.trim()) return { q, type, results: null as SearchResponse | null };
	const results = await api.get<SearchResponse>('/search', {
		query: { q, type, limit_per_type: 20 }
	});
	return { q, type, results };
};
