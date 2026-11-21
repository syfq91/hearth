/**
 * EPUB reader header controls and page tap gestures.
 */
import { expect, test } from '@playwright/test';
import { BOOK_TITLE, seedServer, signIn } from './helpers';

async function openEpub(page: import('@playwright/test').Page): Promise<void> {
	await page.goto('/books');
	await page.getByRole('link', { name: BOOK_TITLE }).first().click();
	await expect(page.getByRole('heading', { level: 1, name: BOOK_TITLE })).toBeVisible();
	// The books spec may have already started this book in the shared server.
	await page.getByRole('link', { name: /^(Read|Resume) EPUB$/ }).first().click();
	await expect(page).toHaveURL(/\/read\?format=epub$/);
	await expect(page.getByRole('button', { name: 'Next page' })).toBeEnabled({ timeout: 30_000 });
}

test('EPUB reader: TOC, settings, header toggle and tap-to-turn', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);
	await openEpub(page);

	const percentage = page.locator('.percentage');
	const viewport = page.locator('.reader-viewport');

	// The header is a transparent gradient overlay, like the video player.
	const headerBackground = await page
		.locator('.reader-header')
		.evaluate((el) => getComputedStyle(el).backgroundColor);
	expect(headerBackground).toBe('rgba(0, 0, 0, 0)');

	// The format badge is gone.
	await expect(page.getByText('EPUB', { exact: true })).toHaveCount(0);
	// The footer shows the chapter and section progress.
	await expect(page.locator('.chapter')).toBeVisible();
	await expect(page.locator('.counter')).toHaveText(/^[1-9]\d* \/ \d+$/);

	// Tapping the right third advances the page and hides the header.
	const before = Number.parseInt((await percentage.textContent()) ?? '0', 10);
	let box = (await viewport.boundingBox())!;
	await page.mouse.click(box.x + box.width * 0.85, box.y + box.height / 2);
	await expect
		.poll(async () => Number.parseInt((await percentage.textContent()) ?? '0', 10))
		.toBeGreaterThan(before);
	await expect(page.locator('.reader-header')).toBeHidden();

	// Tapping the middle toggles the header without resizing the reading area.
	const viewportHeight = (await viewport.boundingBox())!.height;
	box = (await viewport.boundingBox())!;
	await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
	await expect(page.locator('.reader-header')).toBeVisible();
	expect((await viewport.boundingBox())!.height).toBe(viewportHeight);
	box = (await viewport.boundingBox())!;
	await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
	await expect(page.locator('.reader-header')).toBeHidden();
	expect((await viewport.boundingBox())!.height).toBe(viewportHeight);
	box = (await viewport.boundingBox())!;
	await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
	await expect(page.locator('.reader-header')).toBeVisible();

	// The chapter button lists the book's TOC.
	await page.getByRole('button', { name: 'Chapters' }).click();
	const menu = page.getByRole('menu', { name: 'Chapters' });
	await expect(menu).toBeVisible();
	await expect(menu.getByRole('menuitem').first()).toBeVisible();

	// Hiding the header closes any open menu, and a middle tap restores it.
	box = (await viewport.boundingBox())!;
	await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
	await expect(page.locator('.reader-header')).toBeHidden();
	await expect(menu).toBeHidden();
	box = (await viewport.boundingBox())!;
	await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
	await expect(page.locator('.reader-header')).toBeVisible();

	// Settings are applied and persisted.
	await page.getByRole('button', { name: 'Reader settings' }).click();
	const dialog = page.getByRole('dialog', { name: 'Reader settings' });
	await expect(dialog).toBeVisible();
	for (let i = 0; i < 4; i++) await dialog.getByRole('button', { name: 'Increase font size' }).click();
	await expect(dialog.locator('.stepper-value')).toHaveText('22');
	await dialog.getByRole('button', { name: 'Sepia' }).click();
	await dialog.getByRole('button', { name: 'Double' }).click();
	const stored = await page.evaluate(() => localStorage.getItem('hearth:epub-reader-settings'));
	expect(stored).toContain('"fontSize":22');
	expect(stored).toContain('"sepia"');
	expect(stored).toContain('"columns":2');
	await page.keyboard.press('Escape');
	await expect(dialog).toBeHidden();
	await expect(menu).toBeHidden();

	// The header's back arrow returns to the previous page.
	await page.getByRole('link', { name: 'Back' }).click();
	await expect(page).toHaveURL(/\/books\/[^/]+(?:[?#].*)?$/);
});

test('EPUB loading message is centred in the reading area', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);
	// Hold the book response so the loading state stays visible.
	await page.route('**/file/epub*', async (route) => {
		await new Promise((resolve) => setTimeout(resolve, 4_000));
		await route.continue();
	});
	await page.goto('/books');
	await page.getByRole('link', { name: BOOK_TITLE }).first().click();
	await page.getByRole('link', { name: /^(Read|Resume) EPUB$/ }).first().click();

	const status = page.locator('.epub .status');
	await expect(status).toBeVisible();
	const box = (await status.boundingBox())!;
	const area = (await page.locator('.epub-shell').boundingBox())!;
	expect(Math.abs(box.x + box.width / 2 - (area.x + area.width / 2))).toBeLessThan(3);
	expect(Math.abs(box.y + box.height / 2 - (area.y + area.height / 2))).toBeLessThan(3);
});
