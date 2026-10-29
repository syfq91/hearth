import { api } from '../../../lib/api/client';
import type { BookDetail } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
	const book = await api.get<BookDetail>(`/books/${params.bookId}`);
	return { book };
};
