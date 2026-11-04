import { api } from '../../lib/api/client';
import type { SetupStatusResponse } from '../../lib/api/types';
import { redirect } from '@sveltejs/kit';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	try {
		const status = await api.get<SetupStatusResponse>('/auth/setup');
		if (status.has_admin) {
			throw redirect(307, '/login');
		}
	} catch (err) {
		if (err && typeof err === 'object' && 'status' in err && (err as { status: number }).status === 307) {
			throw err;
		}
	}
	return {};
};
