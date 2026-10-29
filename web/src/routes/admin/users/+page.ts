import { api } from '../../../lib/api/client';
import type { UserDetail } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	const users = await api.get<UserDetail[]>('/users');
	return { users };
};
