import { api } from '../../../lib/api/client';
import type { ItemDetail } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
	let item = await api.get<ItemDetail>(`/items/${params.itemId}`);
	if (item.type === 'series') {
		try {
			const nextUp = await api.get<{ id: string }>(`/series/${params.itemId}/next-up`);
			if (nextUp?.id) {
				item = await api.get<ItemDetail>(`/items/${nextUp.id}`);
			}
		} catch {
			/* fallback to item */
		}
	}
	return {
		item,
		// Resume point for movies and episodes alike.
		startAtMs: item.watch_state?.position_ms ?? 0
	};
};
