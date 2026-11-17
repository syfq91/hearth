import { api } from '../../../lib/api/client';
import type { LibraryDetail, LibrarySummary, ScannerSettingsResponse } from '../../../lib/api/types';
import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
	// Skip the short-lived GET cache: creating, editing or deleting a library
	// calls invalidateAll(), which must show the new state immediately instead
	// of replaying the cached list from before the mutation.
	// The list endpoint returns summaries (no paths); the admin view shows the
	// scanned folders, so fetch each library's detail. Admins own a handful of
	// libraries, so the fan-out is small.
	const [summaries, scannerSettings] = await Promise.all([
		api.get<LibrarySummary[]>('/libraries', { cache: false }),
		api.get<ScannerSettingsResponse>('/admin/scanner/settings', { cache: false }).catch(() => null)
	]);
	const libraries = await Promise.all(
		summaries.map((library) => api.get<LibraryDetail>(`/libraries/${library.id}`, { cache: false }))
	);
	return { libraries, scannerSettings };
};
