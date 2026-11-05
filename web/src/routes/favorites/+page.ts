import { api } from '../../lib/api/client';
import type { HomeShelf } from '../../lib/api/types';
import type { PageLoad } from './$types';

// Return the request promise instead of awaiting it: SvelteKit commits the
// navigation immediately and the page renders its own loading state.
export const load: PageLoad = () => {
	const favorites = api.get<HomeShelf>('/home/favorites', { query: { limit: 100 } });
	return { favorites };
};
