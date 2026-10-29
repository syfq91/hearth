import { bootstrap, session } from '../lib/auth.svelte';
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
	return { auth: session.status };
};
