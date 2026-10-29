import { api } from '../lib/api/client';
import type { HealthResponse } from '../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	// Placeholder landing data; the real home shelves land in task 9.3.
	try {
		const health = await api.get<HealthResponse>('/health', { auth: false });
		return { health, error: null };
	} catch (error) {
		return { health: null, error: error instanceof Error ? error.message : String(error) };
	}
};
