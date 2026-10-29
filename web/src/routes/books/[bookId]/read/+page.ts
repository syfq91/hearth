import { api } from '../../../../lib/api/client';
import type { BookDetail, BookProgressResponse } from '../../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params, url }) => {
	const book = await api.get<BookDetail>(`/books/${params.bookId}`);
	const requested = url.searchParams.get('format');
	const format = requested && book.formats.includes(requested) ? requested : book.formats[0] ?? null;
	const progress = await api
		.get<BookProgressResponse>(`/books/${params.bookId}/progress`)
		.catch(() => null);
	return { book, format, progress };
};
