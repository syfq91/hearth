/**
 * EPUB reading: open the reader, turn pages, and come back to a resume point.
 *
 * The reader flushes progress on unmount with an unawaited keepalive PUT
 * (see lib/reader/progress.ts), so the detail page can render before the
 * server has heard about it.
 */
import { expect, test } from '@playwright/test';
import { BOOK_TITLE, seedServer, signIn } from './helpers';

test('read an EPUB, turn pages, and resume later', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	await page.goto('/books');
	// The grid renders the title twice (poster link + heading link), so scope
	// to the first.
	const bookCard = page.getByRole('link', { name: BOOK_TITLE }).first();
	await expect(bookCard).toBeVisible();
	await bookCard.click();
	await expect(page.getByRole('heading', { level: 1, name: BOOK_TITLE })).toBeVisible();
	await page.getByRole('link', { name: 'Read EPUB' }).click();
	await expect(page).toHaveURL(/\/read\?format=epub$/);

	// The reader is ready once it reports a position; the fixture opens on
	// its first chapter, not at 0%.
	const next = page.getByRole('button', { name: 'Next →' });
	const position = page.locator('.position');
	await expect(next).toBeEnabled({ timeout: 30_000 });
	await expect(position).toHaveText(/^\d+%$/, { timeout: 30_000 });
	const opened = Number.parseInt((await position.textContent()) ?? '0', 10);
	expect(opened).toBeGreaterThan(0);

	// Turn exactly one page. The fixture has three short chapters, so walking
	// to the end marks the book Completed and hides the resume link.
	await next.click();
	await expect
		.poll(async () => Number.parseInt((await position.textContent()) ?? '0', 10))
		.toBeGreaterThan(opened);

	// Leave the reader; the breadcrumb on it returns to the detail page.
	await page.getByRole('link', { name: BOOK_TITLE }).first().click();
	await expect(page.getByRole('heading', { level: 1, name: BOOK_TITLE })).toBeVisible();

	// The unmount flush is fired without being awaited, so the first paint may
	// predate it; reload until the server reports the progress we just made.
	await expect(async () => {
		await page.reload();
		await expect(page.getByRole('link', { name: 'Resume EPUB' })).toBeVisible({ timeout: 3_000 });
	}).toPass({ timeout: 30_000 });
	await expect(page.getByText(/% read|Completed/)).toBeVisible();

	// Reopening the reader resumes past where it first opened. foliate-js
	// restores to the containing section's start, which can sit a little
	// before the exact saved percent, so this is a resume-point check rather
	// than an exact-position match.
	await page.getByRole('link', { name: 'Resume EPUB' }).click();
	await expect(next).toBeEnabled({ timeout: 30_000 });
	await expect
		.poll(async () => Number.parseInt((await position.textContent()) ?? '0', 10), {
			timeout: 15_000
		})
		.toBeGreaterThan(opened);
});
