import { api } from '../../../lib/api/client';
import type { SeriesDetail } from '../../../lib/api/types';
import type { PageLoad } from './$types';

// Return the request promise instead of awaiting it: SvelteKit commits the
// navigation immediately and the page renders its own loading state.
export const load: PageLoad = ({ params }) => ({
	detail: api.get<SeriesDetail>(`/series/${params.seriesId}`).then((series) => ({ series }))
});
