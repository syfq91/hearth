import { api } from '../../../../lib/api/client';
import type { BookDetail, BookProgressResponse } from '../../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params, url }) => {
	// Book detail and progress are independent; fetch them in one round trip.
	const [book, progress] = await Promise.all([
		api.get<BookDetail>(`/books/${params.bookId}`),
		api.get<BookProgressResponse>(`/books/${params.bookId}/progress`).catch(() => null)
	]);
	const requested = url.searchParams.get('format');
	const formats = book.formats ?? [];
	const format = requested && formats.includes(requested) ? requested : formats[0] ?? null;
	return { book, format, progress };
};
