/**
 * The primary nav is a shrinkable flex item with non-shrinkable children, so
 * at some widths its last label used to paint under the favourites icon. This
 * checks the painted edges across the widths around the compact breakpoint.
 */
import { expect, test } from '@playwright/test';
import { ensureAccount, signIn } from './helpers';

test('header nav never overlaps the actions cluster', async ({ page, request }) => {
	const pair = await ensureAccount(request);
	const auth = { Authorization: `Bearer ${pair.access_token}` };

	// Four media types produce the widest grouped nav. The shared fixture only
	// creates three libraries, so add one TV library if the server lacks it.
	const response = await request.get('/api/v1/libraries', { headers: auth });
	const libraries = (await response.json()) as { name: string }[];
	if (!libraries.some((library) => library.name === 'Shows')) {
		const created = await request.post('/api/v1/libraries', {
			headers: auth,
			data: { name: 'Shows', type: 'tv', paths: ['/media'] }
		});
		expect(created.ok(), `create Shows library: ${created.status()}`).toBe(true);
	}

	await signIn(page, pair);
	// The heart only renders when a favourite exists; a one-item response
	// reproduces the actions width that caused the overlap.
	await page.route('**/api/v1/home/favorites*', (route) =>
		route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ items: [{}] })
		})
	);
	await page.goto('/');
	await expect(page.locator('header.topbar a[aria-label="Favourites"]')).toBeVisible();

	for (const width of [1280, 1000, 900, 800, 720, 680, 640, 620, 600, 520]) {
		await page.setViewportSize({ width, height: 800 });
		await expect
			.poll(
				() =>
					page.evaluate(() => {
						const nav = document.querySelector('header.topbar nav')!;
						const actions = document.querySelector('header.topbar .topbar-actions')!;
						const items = nav.querySelectorAll(':scope > a, :scope > .lib-dropdown-wrapper');
						const last = items[items.length - 1]!;
						return last.getBoundingClientRect().right <= actions.getBoundingClientRect().left;
					}),
				{ message: `nav overlaps the actions at ${width}px` }
			)
			.toBe(true);
	}
});
