import { api } from '../../../lib/api/client';
import type { LibraryDetail, LibrarySummary } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	// The list endpoint returns summaries (no paths); the admin view shows the
	// scanned folders, so fetch each library's detail. Admins own a handful of
	// libraries, so the fan-out is small.
	const summaries = await api.get<LibrarySummary[]>('/libraries');
	const libraries = await Promise.all(
		summaries.map((library) => api.get<LibraryDetail>(`/libraries/${library.id}`))
	);
	return { libraries };
};
