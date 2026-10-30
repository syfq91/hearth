/**
 * Shared helpers for the Hearth e2e suite.
 *
 * The suite talks to a live server (scripts/e2e.sh starts a Hearth container
 * with web/e2e/fixtures/media mounted read-only at /media). Specs seed through
 * the API and then drive the UI, except `00-first-run.spec.ts`, which walks the
 * whole first-time flow through the UI alone.
 */
import { expect, type APIRequestContext, type Page } from '@playwright/test';

/** The account the first-run spec creates; other specs reuse it. */
export const ADMIN_USERNAME = 'admin';
export const ADMIN_PASSWORD = 'hearth-e2e-admin';

/** Library paths as seen inside the container (fixtures mounted at /media). */
export const LIBRARIES = [
	{ name: 'Movies', type: 'movie', path: '/media/movies' },
	{ name: 'Music', type: 'music', path: '/media/music' },
	{ name: 'Books', type: 'book', path: '/media/books' }
] as const;

export const MOVIE_TITLE = 'Embers at Dawn';
export const ALBUM_TITLE = 'Neon Hollow';
export const ARTIST_NAME = 'The Static Coast';
export const TRACK_TITLE = 'Midnight Signal';
export const BOOK_TITLE = 'Field Notes from Tomorrow';

interface TokenPair {
	access_token: string;
	refresh_token: string;
}

/**
 * Create the admin account, or log in if setup already ran (specs may run in
 * any order against one server; only the first-run spec needs a pristine one).
 */
export async function ensureAccount(request: APIRequestContext): Promise<TokenPair> {
	const setup = await request.post('/api/v1/auth/setup', {
		data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD }
	});
	if (setup.ok()) return (await setup.json()) as TokenPair;
	const login = await request.post('/api/v1/auth/login', {
		data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD }
	});
	expect(login.ok(), `login failed after setup: ${login.status()}`).toBe(true);
	return (await login.json()) as TokenPair;
}

function bearer(pair: TokenPair): Record<string, string> {
	return { Authorization: `Bearer ${pair.access_token}` };
}

/** Create every library in LIBRARIES that does not exist yet. */
export async function createMissingLibraries(request: APIRequestContext, pair: TokenPair): Promise<void> {
	const response = await request.get('/api/v1/libraries', { headers: bearer(pair) });
	const existing = (await response.json()) as { name: string }[];
	const names = new Set(existing.map((library) => library.name));
	for (const library of LIBRARIES) {
		if (names.has(library.name)) continue;
		const response = await request.post('/api/v1/libraries', {
			headers: bearer(pair),
			data: { name: library.name, type: library.type, paths: [library.path] }
		});
		expect(response.ok(), `create ${library.name}: ${response.status()}`).toBe(true);
	}
}

/** Find a library id by name (the list endpoint returns a bare array). */
export async function libraryId(
	request: APIRequestContext,
	pair: TokenPair,
	name: string
): Promise<string> {
	const response = await request.get('/api/v1/libraries', { headers: bearer(pair) });
	const libraries = (await response.json()) as { id: string; name: string }[];
	const found = libraries.find((library) => library.name === name);
	expect(found, `library ${name} not found`).toBeTruthy();
	return found!.id;
}

/**
 * Queue a scan and wait until the library reports at least one item.
 *
 * Each media type has its own list endpoint: `/libraries/{id}/items` only
 * pages movies and series (items.py), books live behind `/books`, and music
 * behind `/albums`. Music and books also don't take a `library_id` on their
 * list endpoints, so those two poll the global list — the suite seeds exactly
 * one library per type.
 */
export async function scanAndWait(
	request: APIRequestContext,
	pair: TokenPair,
	library: (typeof LIBRARIES)[number]
): Promise<void> {
	const id = await libraryId(request, pair, library.name);
	const scan = await request.post(`/api/v1/admin/libraries/${id}/scan`, { headers: bearer(pair) });
	expect(scan.ok(), `scan ${library.name}: ${scan.status()}`).toBe(true);

	const url =
		library.type === 'book'
			? `/api/v1/books?library_id=${id}&limit=1`
			: library.type === 'music'
				? '/api/v1/albums?limit=1'
				: `/api/v1/libraries/${id}/items?limit=1`;

	await expect
		.poll(
			async () => {
				const page = await request.get(url, { headers: bearer(pair) });
				const body = (await page.json()) as { items?: unknown[]; total?: number };
				return body.items?.length ?? body.total ?? 0;
			},
			{ timeout: 60_000, message: `scan of ${library.name} never produced items` }
		)
		.toBeGreaterThan(0);
}

/** Seed the account, libraries and scans that the media specs need. */
export async function seedServer(request: APIRequestContext): Promise<TokenPair> {
	const pair = await ensureAccount(request);
	await createMissingLibraries(request, pair);
	for (const library of LIBRARIES) {
		await scanAndWait(request, pair, library);
	}
	return pair;
}

/**
 * Authenticate an already-open page by seeding the token pair the app stores
 * in localStorage (`src/lib/api/client.ts` hydrates from these on boot).
 */
export async function signIn(page: Page, pair: TokenPair): Promise<void> {
	await page.addInitScript(
		(tokens: TokenPair) => {
			localStorage.setItem('hearth.access', tokens.access_token);
			localStorage.setItem('hearth.refresh', tokens.refresh_token);
		},
		{ access_token: pair.access_token, refresh_token: pair.refresh_token }
	);
}
