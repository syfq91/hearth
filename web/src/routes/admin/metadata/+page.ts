import { api } from '../../../lib/api/client';
import type { MetadataSettingsResponse } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	return {
		settings: await api.get<MetadataSettingsResponse>('/admin/metadata/settings')
	};
};
