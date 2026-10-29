import { api } from '../../../lib/api/client';
import type { ItemDetail } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
	const item = await api.get<ItemDetail>(`/items/${params.itemId}`);
	return { item };
};
