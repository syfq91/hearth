import { api } from '../../../lib/api/client';
import type { TrackListResponse } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ url }) => {
	const query = url.searchParams.get('q') ?? '';
	const tracks = await api.get<TrackListResponse>('/tracks', {
		query: { query: query || undefined, limit: 48 }
	});
	return { tracks, query };
};
