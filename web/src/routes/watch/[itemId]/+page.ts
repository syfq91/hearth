import { api } from '../../../lib/api/client';
import type { ItemDetail } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params, url }) => {
	// Only series have a "next up" hint. Fetching it for every item puts a
	// pointless 404 request for movies on the critical path of the player:
	// the route cannot render until it settles, so a slow/stalled request
	// leaves the user staring at a page where "the video does not play".
	const item = await api.get<ItemDetail>(`/items/${params.itemId}`);
	let resolved = item;
	if (item.type === 'series') {
		try {
			const nextUp = await api.get<{ id: string }>(`/series/${params.itemId}/next-up`);
			if (nextUp?.id) {
				resolved = await api.get<ItemDetail>(`/items/${nextUp.id}`);
			}
		} catch {
			/* fall back to the series item */
		}
	}
	return {
		item: resolved,
		// Resume point for movies and episodes alike, unless the detail page's
		// "play from start" button explicitly requested a fresh playthrough.
		startAtMs: url.searchParams.get('start') === '0' ? 0 : (resolved.watch_state?.position_ms ?? 0)
	};
};
