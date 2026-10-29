import { api } from '../../lib/api/client';
import type { BookListResponse } from '../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	const books = await api.get<BookListResponse>('/books', { query: { limit: 96 } });
	return { books };
};
