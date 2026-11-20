/**
 * Home poster overlays: the play, favourite and mark-played controls rendered
 * over the poster cards in the movie, music and book shelves.
 */
import { expect, test, type Locator } from '@playwright/test';
import { ALBUM_TITLE, BOOK_TITLE, MOVIE_TITLE, seedServer, signIn } from './helpers';

interface Pair {
	access_token: string;
	refresh_token: string;
}

function bearer(pair: Pair): Record<string, string> {
	return { Authorization: `Bearer ${pair.access_token}` };
}

async function toggle(card: Locator, title: string, action: 'played' | 'favorites'): Promise<void> {
	await card.locator('.poster-wrap').hover();
	const name =
		action === 'played'
			? new RegExp(`Mark ${title} as (played|unwatched)`)
			: new RegExp(`(Add ${title} to favorites|Remove ${title} from favorites)`);
	const button = card.getByRole('button', { name });
	const wasPressed = (await button.getAttribute('aria-pressed')) === 'true';
	await button.click();
	await expect(button).toHaveAttribute('aria-pressed', String(!wasPressed));
}

test('movie poster overlay toggles favourite and played state', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);
	await page.goto('/');

	const card = page
		.locator('article.card')
		.filter({ has: page.getByRole('link', { name: `Play ${MOVIE_TITLE}` }) })
		.first();
	await expect(card).toBeVisible();
	await expect(card.locator('a.link')).toHaveAttribute('href', /\/movies\//);
	await expect(card.getByRole('link', { name: `Play ${MOVIE_TITLE}` })).toHaveAttribute(
		'href',
		/\/watch\//
	);

	const poster = card.locator('.poster-wrap');
	const overlay = poster.locator('.overlay');
	await page.mouse.move(0, 0);
	await expect(overlay).toHaveCSS('opacity', '0');
	await poster.hover();
	await expect(overlay).toHaveCSS('opacity', '1');

	await toggle(card, MOVIE_TITLE, 'played');
	await toggle(card, MOVIE_TITLE, 'favorites');
});

test('album poster overlay toggles favourite without a played control', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);
	await page.goto('/');

	const card = page
		.locator('article.album')
		.filter({ has: page.getByRole('link', { name: `Play ${ALBUM_TITLE}` }) })
		.first();
	await expect(card).toBeVisible();
	await expect(
		card.getByRole('button', { name: new RegExp(`Mark ${ALBUM_TITLE} as (played|unwatched)`) })
	).toHaveCount(0);
	await toggle(card, ALBUM_TITLE, 'favorites');
});

test('book cover overlay toggles favourite and read state', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	// "Continue reading" only lists books with progress, so seed some first.
	const books = (await (await request.get('/api/v1/books', { headers: bearer(pair) })).json()) as {
		items: { id: string; title: string }[];
	};
	const book = books.items.find((entry) => entry.title === BOOK_TITLE);
	expect(book, `${BOOK_TITLE} was not seeded`).toBeTruthy();
	const progress = await request.put(`/api/v1/books/${book!.id}/progress`, {
		headers: bearer(pair),
		data: { locator: '{}', percentage: 25, completed: false }
	});
	expect(progress.ok()).toBe(true);

	await page.goto('/');

	const card = page
		.locator('article.book')
		.filter({ has: page.getByRole('link', { name: `Play ${BOOK_TITLE}` }) })
		.first();
	await expect(card).toBeVisible();
	await toggle(card, BOOK_TITLE, 'played');
	await toggle(card, BOOK_TITLE, 'favorites');
});
