import { api } from '../../lib/api/client';
import type { LibrarySummary } from '../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	const libraries = await api.get<LibrarySummary[]>('/libraries');
	return { libraries };
};
