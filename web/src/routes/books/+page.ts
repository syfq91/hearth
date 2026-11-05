import { api } from '../../lib/api/client';
import type { BookListResponse } from '../../lib/api/types';
import type { PageLoad } from './$types';

// Return the request promise instead of awaiting it: SvelteKit commits the
// navigation immediately and the page renders its own loading state.
export const load: PageLoad = () => {
	const books = api.get<BookListResponse>('/books', { query: { limit: 96 } });
	return { books };
};
