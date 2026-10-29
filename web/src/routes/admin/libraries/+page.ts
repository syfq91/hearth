import { api } from '../../../lib/api/client';
import type { LibraryDetail } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	const libraries = await api.get<LibraryDetail[]>('/libraries');
	return { libraries };
};
