/**
 * Music: find an album, play a track, and see the audio bar drive playback.
 *
 * The AudioBar owns a single persistent <audio> element; the spec asserts the
 * element is really advancing (not just that the bar rendered).
 */
import { expect, test } from '@playwright/test';
import { ALBUM_TITLE, ARTIST_NAME, TRACK_TITLE, seedServer, signIn } from './helpers';

test('play a track from an album', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	await page.goto('/music');
	await page.getByRole('link', { name: ALBUM_TITLE }).first().click();
	await expect(page.getByRole('heading', { level: 1, name: ALBUM_TITLE })).toBeVisible();

	// The album page links to the artist screen too.
	await expect(page.getByRole('link', { name: ARTIST_NAME })).toBeVisible();

	await page.getByRole('button', { name: TRACK_TITLE }).click();

	const bar = page.getByRole('region', { name: 'Now playing' });
	await expect(bar).toBeVisible();
	await expect(bar.getByText(TRACK_TITLE)).toBeVisible();

	const audio = page.locator('audio');
	await expect
		.poll(() => audio.evaluate((el: HTMLAudioElement) => el.currentTime), { timeout: 30_000 })
		.toBeGreaterThan(0.5);

	// Pause from the bar.
	await bar.getByRole('button', { name: 'Pause' }).click();
	await expect.poll(() => audio.evaluate((el: HTMLAudioElement) => el.paused)).toBe(true);
});

test('the artists screen lists the scanned artist', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	await page.goto('/music/artists');
	await expect(page.getByRole('link', { name: new RegExp(ARTIST_NAME) }).first()).toBeVisible();
});
