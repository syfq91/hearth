import { bootstrap, session } from '../lib/auth.svelte';
import { api } from '../lib/api/client';
import type { LibrarySummary } from '../lib/api/types';
import { redirect } from '@sveltejs/kit';
import type { LayoutLoad } from './$types';

export const ssr = false;

/** Routes reachable without a session. */
const PUBLIC_ROUTES = ['/login', '/setup'];

interface NavData {
	libraries: LibrarySummary[];
	hasFavorites: boolean;
}

async function loadNavData(): Promise<NavData> {
	// Neither request may hold up navigation. `api.get` already coalesces and
	// caches them; the promise is passed to the layout and resolved in place.
	const [libsResult, favsResult] = await Promise.allSettled([
		api.get<LibrarySummary[]>('/libraries'),
		api.get<{ items: unknown[] }>('/home/favorites', { query: { limit: 1 } })
	]);
	return {
		libraries: libsResult.status === 'fulfilled' ? libsResult.value : [],
		hasFavorites: favsResult.status === 'fulfilled' && favsResult.value.items.length > 0
	};
}

export const load: LayoutLoad = async ({ url }) => {
	await bootstrap();

	const path = url.pathname;
	const isPublic = PUBLIC_ROUTES.some((route) => path === route || path.startsWith(`${route}/`));

	if (session.status === 'anonymous' && !isPublic) {
		throw redirect(307, '/login');
	}
	if (session.status === 'authenticated' && isPublic) {
		throw redirect(307, '/');
	}
	// Admin screens are admin-only; everyone else lands back home.
	if (
		session.status === 'authenticated' &&
		path.startsWith('/admin') &&
		session.user?.role !== 'admin'
	) {
		throw redirect(307, '/');
	}

	const nav: Promise<NavData> =
		session.status === 'authenticated' && !isPublic
			? loadNavData()
			: Promise.resolve({ libraries: [], hasFavorites: false });

	return { auth: session.status, nav };
};
