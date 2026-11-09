import { bootstrap, session } from '../lib/auth.svelte';
import { api } from '../lib/api/client';
import type { LibrarySummary } from '../lib/api/types';
import { redirect } from '@sveltejs/kit';
import type { LayoutLoad } from './$types';

export const ssr = false;

/** Routes reachable without a session. */
const PUBLIC_ROUTES = ['/login', '/setup'];

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

	let libraries: LibrarySummary[] = [];
	if (session.status === 'authenticated' && !isPublic) {
		try {
			libraries = await api.get<LibrarySummary[]>('/libraries');
		} catch {
			libraries = [];
		}
	}

	return { auth: session.status, libraries };
};
