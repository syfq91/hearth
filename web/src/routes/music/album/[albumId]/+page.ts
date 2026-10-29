import { api } from '../../../../lib/api/client';
import type { AlbumDetail } from '../../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
	const album = await api.get<AlbumDetail>(`/albums/${params.albumId}`);
	return { album };
};
