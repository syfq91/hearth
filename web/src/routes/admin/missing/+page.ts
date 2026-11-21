import { api } from '../../../lib/api/client';
import type { MissingFilePage } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	const page = await api.get<MissingFilePage>('/admin/missing-files', {
		query: { limit: 50, offset: 0 },
		cache: false
	});
	return { page };
};
