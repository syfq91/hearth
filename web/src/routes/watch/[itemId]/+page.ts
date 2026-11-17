import { api } from '../../../lib/api/client';
import type { ItemDetail } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
	// Fetch the item and its next-up hint together; only the follow-up item
	// detail (when there is a next episode) needs a second round trip.
	const [item, nextUp] = await Promise.all([
		api.get<ItemDetail>(`/items/${params.itemId}`),
		api.get<{ id: string }>(`/series/${params.itemId}/next-up`).catch(() => null)
	]);
	let resolved = item;
	if (item.type === 'series' && nextUp?.id) {
		try {
			resolved = await api.get<ItemDetail>(`/items/${nextUp.id}`);
		} catch {
			/* fall back to the series item */
		}
	}
	return {
		item: resolved,
		// Resume point for movies and episodes alike.
		startAtMs: resolved.watch_state?.position_ms ?? 0
	};
};
