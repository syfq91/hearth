/**
 * EPUB reader header controls and page tap gestures.
 */
import { expect, test } from '@playwright/test';
import { BOOK_TITLE, seedServer, signIn } from './helpers';

async function openEpub(page: import('@playwright/test').Page): Promise<void> {
	await page.goto('/books');
	await page.getByRole('link', { name: BOOK_TITLE }).first().click();
	await expect(page.getByRole('heading', { level: 1, name: BOOK_TITLE })).toBeVisible();
	await page.getByRole('link', { name: 'Read EPUB' }).click();
	await expect(page).toHaveURL(/\/read\?format=epub$/);
	await expect(page.getByRole('button', { name: 'Next page' })).toBeEnabled({ timeout: 30_000 });
}

test('EPUB reader: TOC, settings, header toggle and tap-to-turn', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);
	await openEpub(page);

	const percentage = page.locator('.percentage');
	const viewport = page.locator('.reader-viewport');

	// The format badge is gone.
	await expect(page.getByText('EPUB', { exact: true })).toHaveCount(0);
	// The footer shows the chapter and section progress.
	await expect(page.locator('.chapter')).toBeVisible();
	await expect(page.locator('.counter')).toContainText('/');

	// Tapping the right third advances the page.
	const before = Number.parseInt((await percentage.textContent()) ?? '0', 10);
	let box = (await viewport.boundingBox())!;
	await page.mouse.click(box.x + box.width * 0.85, box.y + box.height / 2);
	await expect
		.poll(async () => Number.parseInt((await percentage.textContent()) ?? '0', 10))
		.toBeGreaterThan(before);

	// Tapping the middle toggles the header, and tapping again restores it.
	box = (await viewport.boundingBox())!;
	await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
	await expect(page.locator('.reader-header')).toHaveCount(0);
	box = (await viewport.boundingBox())!;
	await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
	await expect(page.locator('.reader-header')).toBeVisible();

	// The chapter button lists the book's TOC.
	await page.getByRole('button', { name: 'Chapters' }).click();
	const menu = page.getByRole('menu', { name: 'Chapters' });
	await expect(menu).toBeVisible();
	await expect(menu.getByRole('menuitem').first()).toBeVisible();

	// Settings are applied and persisted.
	await page.getByRole('button', { name: 'Reader settings' }).click();
	const dialog = page.getByRole('dialog', { name: 'Reader settings' });
	await expect(dialog).toBeVisible();
	await dialog.getByRole('button', { name: 'Font size 22' }).click();
	await dialog.getByRole('button', { name: 'Sepia' }).click();
	const stored = await page.evaluate(() => localStorage.getItem('hearth:epub-reader-settings'));
	expect(stored).toContain('"fontSize":22');
	expect(stored).toContain('"sepia"');
	await page.keyboard.press('Escape');
	await expect(dialog).toBeHidden();
	await expect(menu).toBeHidden();
});
