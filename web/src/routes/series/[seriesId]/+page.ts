import { api } from '../../../lib/api/client';
import type { SeriesDetail } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ params }) => {
	const series = await api.get<SeriesDetail>(`/series/${params.seriesId}`);
	return { series };
};
