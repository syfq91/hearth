/**
 * Favorites: music cards show the parent album's art, the art starts playback,
 * and the title opens the album instead of treating the track id as an album.
 */
import { expect, test } from '@playwright/test';
import { ALBUM_TITLE, TRACK_TITLE, seedServer, signIn } from './helpers';

interface TrackSummary {
	id: string;
	album_id: string | null;
	title: string;
}

test('favorite music shows art, plays from the art and links to the album', async ({
	page,
	request
}) => {
	const pair = await seedServer(request);
	const headers = { Authorization: `Bearer ${pair.access_token}` };

	const tracksResponse = await request.get('/api/v1/tracks?query=Midnight', { headers });
	expect(tracksResponse.ok()).toBe(true);
	const { items } = (await tracksResponse.json()) as { items: TrackSummary[] };
	const track = items.find((item) => item.title === TRACK_TITLE);
	expect(track, 'fixture track not found').toBeTruthy();
	expect(track!.album_id).toBeTruthy();

	const favorite = await request.post(`/api/v1/items/${track!.id}/favorite`, {
		headers,
		data: { favorite: true }
	});
	expect(favorite.ok()).toBe(true);

	await signIn(page, pair);
	await page.goto('/favorites');

	const card = page.locator('article.card').filter({ has: page.getByRole('link', { name: TRACK_TITLE }) });
	await expect(card).toBeVisible();
	const art = card.getByRole('button', { name: `Play ${TRACK_TITLE}` });
	await expect(art.locator('img')).toBeVisible();
	await expect(art.locator('img')).toHaveAttribute('src', /\/api\/v1\/images\//);

	// The title navigates to the album, never to /music/album/<track id>.
	await card.getByRole('link', { name: TRACK_TITLE }).click();
	await expect(page).toHaveURL(new RegExp(`/music/album/${track!.album_id}$`));
	await expect(page.getByRole('heading', { level: 1, name: ALBUM_TITLE })).toBeVisible();

	// Clicking the art starts the favorited song without navigating away.
	await page.goto('/favorites');
	const artAgain = page
		.locator('article.card')
		.filter({ has: page.getByRole('link', { name: TRACK_TITLE }) })
		.getByRole('button', { name: `Play ${TRACK_TITLE}` });
	await artAgain.click();
	const bar = page.getByRole('region', { name: 'Now playing' });
	await expect(bar).toBeVisible();
	await expect(bar.getByText(TRACK_TITLE)).toBeVisible();
});
