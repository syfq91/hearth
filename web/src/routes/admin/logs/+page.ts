import { api } from '../../../lib/api/client';
import type { LogEntryResponse } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	try {
		const logs = await api.get<LogEntryResponse[]>('/admin/logs', {
			query: { limit: 100 }
		});
		return {
			logs,
			error: null
		};
	} catch (e) {
		return {
			logs: [],
			error: e instanceof Error ? e.message : String(e)
		};
	}
};
