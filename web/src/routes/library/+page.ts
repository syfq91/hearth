import { api } from '../../lib/api/client';
import type { LibrarySummary } from '../../lib/api/types';
import type { PageLoad } from './$types';

// Return the request promise instead of awaiting it: SvelteKit commits the
// navigation immediately and the page renders its own loading state.
export const load: PageLoad = () => {
	const libraries = api.get<LibrarySummary[]>('/libraries');
	return { libraries };
};
