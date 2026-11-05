import { api } from '../../lib/api/client';
import type { SearchResponse } from '../../lib/api/types';
import type { PageLoad } from './$types';

// Return the request promise instead of awaiting it: SvelteKit commits the
// navigation immediately and the page renders its own loading state.
export const load: PageLoad = ({ url }) => {
	const q = url.searchParams.get('q') ?? '';
	const type = url.searchParams.get('type') ?? 'all';
	const results = q.trim()
		? api.get<SearchResponse>('/search', {
				query: { q, type, limit_per_type: 20 }
			})
		: null;
	return { q, type, results };
};
