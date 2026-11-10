import { api } from '../../../lib/api/client';
import type { PluginSummary } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	return {
		plugins: await api.get<PluginSummary[]>('/plugins')
	};
};
