import { api } from '../../../../lib/api/client';
import type { ArtistDetail } from '../../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
	const artist = await api.get<ArtistDetail>(`/artists/${params.artistId}`);
	return { artist };
};
