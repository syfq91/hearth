/**
 * Music: find an album, play a track, and see the audio bar drive playback.
 *
 * The AudioBar owns a single persistent <audio> element; the spec asserts the
 * element is really advancing (not just that the bar rendered).
 */
import { expect, test } from '@playwright/test';
import {
	ADMIN_USERNAME,
	ALBUM_TITLE,
	ARTIST_NAME,
	TRACK_TITLE,
	seedServer,
	signIn
} from './helpers';

test('play a track from an album', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	await page.goto('/music');
	await page.getByRole('link', { name: ALBUM_TITLE }).first().click();
	await expect(page.getByRole('heading', { level: 1, name: ALBUM_TITLE })).toBeVisible();

	// The album page links to the artist screen too.
	await expect(page.getByRole('link', { name: ARTIST_NAME })).toBeVisible();

	await page.getByRole('button', { name: TRACK_TITLE, exact: true }).click();

	const bar = page.getByRole('region', { name: 'Now playing' });
	await expect(bar).toBeVisible();
	await expect(bar.getByText(TRACK_TITLE)).toBeVisible();
	await expect(bar.getByText(/-\d+:\d{2}/)).toBeVisible();

	const audio = page.locator('audio');
	await expect
		.poll(() => audio.evaluate((el: HTMLAudioElement) => el.currentTime), { timeout: 30_000 })
		.toBeGreaterThan(0.5);

	// Pause from the bar.
	await bar.getByRole('button', { name: 'Pause' }).click();
	await expect.poll(() => audio.evaluate((el: HTMLAudioElement) => el.paused)).toBe(true);
});

test('album actions shuffle playback and toggle the album favorite', async ({
	page,
	request
}) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	await page.goto('/music');
	await page.getByRole('link', { name: ALBUM_TITLE }).first().click();

	const shuffle = page.getByRole('button', { name: 'Shuffle album' });
	const favorite = page.getByRole('button', { name: 'Add album to favorites' });
	await expect(shuffle).toBeVisible();
	await expect(favorite).toBeVisible();
	await expect(favorite).toHaveAttribute('aria-pressed', 'false');

	await favorite.click();
	const unfavorite = page.getByRole('button', { name: 'Remove album from favorites' });
	await expect(unfavorite).toHaveAttribute('aria-pressed', 'true');

	// The toggle persists server-side, not just in local state.
	await page.reload();
	await expect(
		page.getByRole('button', { name: 'Remove album from favorites' })
	).toHaveAttribute('aria-pressed', 'true');

	await shuffle.click();
	const bar = page.getByRole('region', { name: 'Now playing' });
	await expect(bar).toBeVisible();
	await expect(bar.getByRole('button', { name: 'Shuffle on' })).toBeVisible();
});

test('album track menu queues a track and inserts another next', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	await page.goto('/music');
	await page.getByRole('link', { name: ALBUM_TITLE }).first().click();

	const more = page.getByRole('button', { name: `More options for ${TRACK_TITLE}` });
	await more.click();
	await page.getByRole('menuitem', { name: 'Add to queue' }).click();

	const bar = page.getByRole('region', { name: 'Now playing' });
	await expect(bar).toBeVisible();
	// Adding while idle prepares the track but does not start playback.
	await expect(page.locator('audio')).toHaveJSProperty('paused', true);

	await more.click();
	await page.getByRole('menuitem', { name: 'Play next' }).click();
	await more.click();
	await page.getByRole('menuitem', { name: 'Add to queue' }).click();
	await bar.getByRole('button', { name: 'Open full screen player' }).click();
	await expect(page.getByRole('heading', { name: /Queue/ })).toContainText('3');

	await page.getByRole('button', { name: 'Minimize player' }).click();
	await expect(bar.getByRole('button', { name: 'Play', exact: true })).toBeVisible();
});

test('album poster menu queues the whole album', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	await page.goto('/music');
	const poster = page.locator('.poster-wrap').first();
	await poster.hover();
	await page.getByRole('button', { name: `More options for ${ALBUM_TITLE}` }).click();
	await page.getByRole('menuitem', { name: 'Add to queue' }).click();

	const bar = page.getByRole('region', { name: 'Now playing' });
	await expect(bar).toBeVisible();
	await expect(page.locator('audio')).toHaveJSProperty('paused', true);

	await poster.hover();
	await page.getByRole('button', { name: `More options for ${ALBUM_TITLE}` }).click();
	await page.getByRole('menuitem', { name: 'Play next' }).click();
	await bar.getByRole('button', { name: 'Open full screen player' }).click();
	await expect(page.getByRole('heading', { name: /Queue/ })).toContainText('2');
	await expect(
		page.getByRole('dialog', { name: 'Full screen player' }).getByText(/-\d+:\d{2}/)
	).toBeVisible();
});

test('queueing from idle never starts playback', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	await page.addInitScript(() => {
		const state = { plays: 0 };
		(window as Window & { __hearthPlayCalls?: typeof state }).__hearthPlayCalls = state;
		const original = HTMLMediaElement.prototype.play;
		HTMLMediaElement.prototype.play = function () {
			state.plays += 1;
			return original.call(this);
		};
	});

	await page.goto('/music');
	const poster = page.locator('.poster-wrap').first();
	const bar = page.getByRole('region', { name: 'Now playing' });

	await poster.hover();
	await page.getByRole('button', { name: `More options for ${ALBUM_TITLE}` }).click();
	await page.getByRole('menuitem', { name: 'Add to queue' }).click();
	await expect(bar).toBeVisible();
	await expect(page.locator('audio')).toHaveJSProperty('paused', true);

	// A second action while the first stream token is still resolving must not
	// re-enter the play path either.
	await poster.hover();
	await page.getByRole('button', { name: `More options for ${ALBUM_TITLE}` }).click();
	await page.getByRole('menuitem', { name: 'Play next' }).click();
	await expect(bar.getByRole('button', { name: 'Play', exact: true })).toBeVisible();

	const plays = await page.evaluate(
		() =>
			(window as Window & { __hearthPlayCalls?: { plays: number } }).__hearthPlayCalls?.plays ??
			-1
	);
	expect(plays).toBe(0);
});

test('the artists screen lists the scanned artist', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	await page.goto('/music/artists');
	await expect(page.getByRole('link', { name: new RegExp(ARTIST_NAME) }).first()).toBeVisible();
});

test('ReplayGain is opt-in so plain playback survives backgrounding', async ({
	page,
	request
}) => {
	const pair = await seedServer(request);
	await signIn(page, pair);

	// Routing <audio> through an AudioContext is what stops music in the
	// background on mobile. Count contexts to prove the default path is direct.
	await page.addInitScript(() => {
		const state = { contexts: 0 };
		(window as Window & { __hearthAudioContexts?: typeof state }).__hearthAudioContexts = state;
		const Original = window.AudioContext;
		if (Original) {
			window.AudioContext = class extends Original {
				constructor(...args: ConstructorParameters<typeof AudioContext>) {
					super(...args);
					state.contexts += 1;
				}
			};
		}
	});
	const contextCount = () =>
		page.evaluate(
			() =>
				(window as Window & { __hearthAudioContexts?: { contexts: number } })
					.__hearthAudioContexts?.contexts ?? -1
		);

	await page.goto('/music');
	await page.getByRole('link', { name: ALBUM_TITLE }).first().click();
	await page.getByRole('button', { name: TRACK_TITLE, exact: true }).click();

	const bar = page.getByRole('region', { name: 'Now playing' });
	await expect(bar).toBeVisible();
	await expect
		.poll(() => page.locator('audio').evaluate((el: HTMLAudioElement) => el.currentTime), {
			timeout: 30_000
		})
		.toBeGreaterThan(0.5);
	expect(await contextCount()).toBe(0);

	// The toggle lives with the user's settings, not in the player chrome.
	// Navigate within the SPA so the playing track survives the route change.
	await page.getByRole('button', { name: ADMIN_USERNAME }).click();
	await page.getByRole('menuitem', { name: 'Profile' }).click();
	const replayGain = page.getByRole('checkbox', { name: 'ReplayGain volume leveling' });
	await expect(replayGain).not.toBeChecked();
	await replayGain.check();
	await expect(replayGain).toBeChecked();
	await expect.poll(contextCount).toBeGreaterThan(0);

	await page.reload();
	expect(await page.evaluate(() => localStorage.getItem('hearth.replay_gain'))).toBe('1');
});
