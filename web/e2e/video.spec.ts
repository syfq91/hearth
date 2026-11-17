/**
 * Video playback: play, seek with the arrow key, pause, leave, and resume
 * from where playback stopped.
 *
 * Progress checkpoints (docs/development/api.md §4) are written every 15s of playback and
 * immediately on pause/unload, so each step pauses before navigating away.
 */
import { expect, test, type Page } from '@playwright/test';
import { MOVIE_TITLE, seedServer, signIn } from './helpers';

/**
 * Open the movie's detail page. Once the movie has progress, Home's "Continue
 * watching" shelf links straight to /watch, so match the detail href directly
 * instead of relying on a title locator's first match.
 */
async function openMovieDetail(page: Page): Promise<void> {
	await page.goto('/');
	const detail = page.locator('a[href^="/movies/"]').filter({ hasText: MOVIE_TITLE }).first();
	await expect(detail).toBeVisible();
	await detail.click();
	await expect(page).toHaveURL(/\/movies\//);
}

test('play, seek and pause a movie', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	await openMovieDetail(page);
	// The movie already has progress if an earlier spec (or first-run) played
	// it, so the action reads Resume rather than Play.
	await page.getByRole('link', { name: /^(Play|Resume)$/ }).click();

	const video = page.locator('video');
	await expect(video).toBeVisible();
	await expect
		.poll(() => video.evaluate((el: HTMLVideoElement) => el.currentTime), { timeout: 30_000 })
		.toBeGreaterThan(1);

	// ArrowRight seeks forward 10s (media keys are scoped to the video).
	const before = await video.evaluate((el: HTMLVideoElement) => el.currentTime);
	await video.press('ArrowRight');
	await expect
		.poll(() => video.evaluate((el: HTMLVideoElement) => el.currentTime))
		.toBeGreaterThan(before + 8);

	// Space pauses (the onpause handler writes progress immediately).
	await video.press(' ');
	await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.paused)).toBe(true);
});

test('resume picks up where playback stopped', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	await openMovieDetail(page);
	const detailUrl = page.url();
	await page.getByRole('link', { name: /^(Play|Resume)$/ }).click();

	const video = page.locator('video');
	await expect(video).toBeVisible();
	await expect
		.poll(() => video.evaluate((el: HTMLVideoElement) => el.currentTime), { timeout: 30_000 })
		.toBeGreaterThan(4);

	// Pause (writes progress) and leave the player.
	await video.press(' ');
	await expect.poll(() => video.evaluate((el: HTMLVideoElement) => el.paused)).toBe(true);
	const stoppedAt = await video.evaluate((el: HTMLVideoElement) => el.currentTime);

	// The player's "Home" control goes back to the detail page it was opened
	// from, which unmounts the player and writes the stop checkpoint.
	await page.getByRole('link', { name: 'Home' }).click();
	await expect(page).toHaveURL(/\/movies\//);

	// Reload so the detail page re-reads the saved checkpoint. Resume restarts
	// near it (the player skips the jump only within 30s of the end, and the
	// fixture is 75s long).
	await page.goto(detailUrl);
	await page.getByRole('link', { name: 'Resume', exact: true }).click();
	const resumed = page.locator('video');
	await expect(resumed).toBeVisible();
	await expect
		.poll(() => resumed.evaluate((el: HTMLVideoElement) => el.currentTime), { timeout: 30_000 })
		.toBeGreaterThan(stoppedAt - 5);
});

test('playback recovers after 503 burst without request storms (T11)', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	let burst503Count = 0;
	const requestTimes: number[] = [];

	// Intercept HLS segments to force a burst of 503s with Retry-After: 1
	await page.route('**/api/v1/hls/**/seg_*.m4s*', async (route) => {
		requestTimes.push(Date.now());
		if (burst503Count < 2) {
			burst503Count++;
			await route.fulfill({
				status: 503,
				headers: {
					'Retry-After': '1',
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					error: {
						code: 'transcode_timeout',
						message: 'Segment generation in progress; retry shortly'
					}
				})
			});
			return;
		}
		await route.continue();
	});

	await openMovieDetail(page);
	await page.getByRole('link', { name: /^(Play|Resume)$/ }).click();

	const video = page.locator('video');
	await expect(video).toBeVisible();
	await expect
		.poll(() => video.evaluate((el: HTMLVideoElement) => el.currentTime), { timeout: 30_000 })
		.toBeGreaterThan(1);

	// If HLS segments were requested and hit 503s, verify request intervals >= 700ms (no storm)
	if (requestTimes.length >= 2) {
		for (let i = 1; i < Math.min(requestTimes.length, burst503Count + 1); i++) {
			const diff = requestTimes[i] - requestTimes[i - 1];
			expect(diff).toBeGreaterThanOrEqual(700);
		}
	}

	// Navigate away to trigger stop beacon on unmount/pagehide. The player's
	// "Home" control returns to the detail page it was opened from.
	await page.getByRole('link', { name: 'Home' }).click();
	await expect(page).toHaveURL(/\/movies\//);
});
