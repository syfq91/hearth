import { api } from '../../../lib/api/client';
import type { ItemPage, LibrarySummary } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
	const library = await api.get<LibrarySummary>(`/libraries/${params.libraryId}`);
	return { library };
};
