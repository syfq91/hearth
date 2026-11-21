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
});
