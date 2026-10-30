/**
 * The Phase 9 acceptance flow: a first-time user goes from a pristine server
 * to watching a video using only the UI.
 *
 *   setup account → create libraries → scan → play the movie
 *
 * It runs first (see scripts/e2e.sh) and against a fresh container: the setup
 * endpoint refuses once any account exists.
 */
import { expect, test } from '@playwright/test';
import { ADMIN_PASSWORD, ADMIN_USERNAME, LIBRARIES, MOVIE_TITLE } from './helpers';

test('first-time user sets up Hearth and watches a video', async ({ page, request }) => {
	// A pristine server has no account at all, so a login attempt is a
	// non-destructive probe (the setup POST would consume the one-shot setup).
	const probe = await request.post('/api/v1/auth/login', {
		data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD }
	});
	test.skip(probe.ok(), 'setup already completed; run against a fresh container');

	// 1. Anonymous visitors land on the login page, which offers setup.
	await page.goto('/');
	await expect(page).toHaveURL(/\/login$/);
	await page.getByRole('link', { name: 'Set up the admin account' }).click();
	// Client-side navigation is async: wait for the destination before typing,
	// or a fill can land on the outgoing form and be discarded on unmount.
	await expect(page).toHaveURL(/\/setup$/);

	// 2. Create the admin account.
	await page.locator('input[name="username"]').fill(ADMIN_USERNAME);
	await page.locator('input[name="password"]').fill(ADMIN_PASSWORD);
	await page.locator('input[name="confirm"]').fill(ADMIN_PASSWORD);
	await page.getByRole('button', { name: 'Create admin account' }).click();
	await expect(page).toHaveURL(/\/$/);
	await expect(page.getByRole('heading', { name: 'Home', level: 1 })).toBeVisible();

	// 3. The empty home page points at library setup.
	await page.getByRole('link', { name: 'Set up libraries' }).click();
	await expect(page).toHaveURL(/\/admin\/libraries$/);

	// 4. Create one library per media type, through the form.
	for (const library of LIBRARIES) {
		await page.locator('input[placeholder="Movies"]').fill(library.name);
		await page.locator('select').selectOption(library.type);
		await page.locator('textarea').fill(library.path);
		await page.getByRole('button', { name: 'Create library' }).click();
		await expect(page.getByRole('status')).toContainText(`Library “${library.name}” created.`);
	}

	// 5. Kick a scan on each library. The status message only renders when the
	// POST resolved (a failure renders role="alert" instead).
	for (const library of LIBRARIES) {
		const row = page.locator('li', { hasText: library.name }).last();
		await row.getByRole('button', { name: 'Scan', exact: true }).click();
		await expect(page.getByRole('status')).toContainText('Scan queued');
	}

	// 6. Wait for the scan to surface the movie on Home (scan is a job).
	const movieCard = page.getByRole('link', { name: MOVIE_TITLE });
	await expect(async () => {
		await page.goto('/');
		await expect(movieCard).toBeVisible({ timeout: 5_000 });
	}).toPass({ timeout: 90_000 });

	// 7. Open the movie and play it.
	await movieCard.first().click();
	await expect(page).toHaveURL(/\/movies\//);
	await expect(page.getByRole('heading', { level: 1, name: MOVIE_TITLE })).toBeVisible();
	await page.getByRole('link', { name: 'Play', exact: true }).click();
	await expect(page).toHaveURL(/\/watch\//);

	// 8. Playback actually starts (autoplay is permitted in the test profile).
	const video = page.locator('video');
	await expect(video).toBeVisible();
	await expect
		.poll(() => video.evaluate((el: HTMLVideoElement) => el.currentTime), { timeout: 30_000 })
		.toBeGreaterThan(1);
});
