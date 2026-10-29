import { api } from '../../../lib/api/client';
import type { SearchResultResponse, UnmatchedItemResponse } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	const unmatched = await api.get<UnmatchedItemResponse[]>('/admin/unmatched', {
		query: { limit: 50 }
	});
	return { unmatched };
};
