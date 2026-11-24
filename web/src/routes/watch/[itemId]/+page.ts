import { api } from '../../../lib/api/client';
import type { ItemDetail } from '../../../lib/api/types';
import type { PageLoad } from './$types';

// Resolve the whole route payload as one promise so navigation commits
// immediately. Only series need the extra "next up" lookup, and it stays off
// the critical path for movies and episodes.
export const load: PageLoad = ({ params, url }) => ({
	detail: (async () => {
		let item = await api.get<ItemDetail>(`/items/${params.itemId}`);
		if (item.type === 'series') {
			try {
				const nextUp = await api.get<{ id: string }>(`/series/${params.itemId}/next-up`);
				if (nextUp?.id) {
					item = await api.get<ItemDetail>(`/items/${nextUp.id}`);
				}
			} catch {
				/* fall back to the series item */
			}
		}
		return {
			item,
			// Resume point for movies and episodes alike, unless the detail
			// page's "play from start" button asked for a fresh playthrough.
			startAtMs: url.searchParams.get('start') === '0' ? 0 : (item.watch_state?.position_ms ?? 0)
		};
	})()
});
