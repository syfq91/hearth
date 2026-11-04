import { api } from '../../lib/api/client';
import type { SetupStatusResponse } from '../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	try {
		const status = await api.get<SetupStatusResponse>('/auth/setup');
		return {
			hasAdmin: status.has_admin
		};
	} catch {
		return {
			hasAdmin: true
		};
	}
};
