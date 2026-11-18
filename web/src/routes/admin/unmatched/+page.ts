import { api } from '../../../lib/api/client';
import type { UnmatchedItemResponse } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	const [unmatched, providers] = await Promise.all([
		api.get<UnmatchedItemResponse[]>('/admin/unmatched', {
			query: { limit: 50 }
		}),
		api.get<string[]>('/metadata/providers')
	]);
	return { unmatched, providers };
};
