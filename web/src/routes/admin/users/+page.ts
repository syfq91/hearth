import { api } from '../../../lib/api/client';
import type { LibrarySummary, UserDetail } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	const [users, libraries] = await Promise.all([
		api.get<UserDetail[]>('/users'),
		api.get<LibrarySummary[]>('/libraries')
	]);
	return { users, libraries };
};
