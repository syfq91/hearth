/**
 * Comic reader footer layout, page modes, wide pages, reading direction and
 * keyboard page turns.
 */
import { expect, test } from '@playwright/test';
import { seedServer, signIn } from './helpers';

async function showHeader(page: import('@playwright/test').Page): Promise<void> {
	const area = (await page.locator('.comic .page-area').boundingBox())!;
	await page.mouse.click(area.x + area.width / 2, area.y + area.height / 2);
	await expect(page.locator('.reader-header')).toBeVisible();
}

test('comic reader: footer edges, spreads, wide pages, direction and arrows', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);
	await page.goto('/books');
	await page.getByRole('link', { name: 'Sample Comic' }).first().click();
	await page.getByRole('link', { name: /^(Read|Resume) CBZ$/ }).first().click();
	await expect(page.getByRole('button', { name: 'Previous page' })).toBeVisible();

	const footer = page.locator('.comic .controls');
	const percentage = footer.locator('.percentage');
	const counter = footer.locator('.counter');
	const images = page.locator('.comic .pages img');
	await expect(percentage).toHaveText('20%');
	await expect(counter).toHaveText('1 / 5');

	// The footer spans the viewport so progress sits at the outer edges.
	const footerBox = (await footer.boundingBox())!;
	const percentBox = (await percentage.boundingBox())!;
	const counterBox = (await counter.boundingBox())!;
	expect(Math.abs(percentBox.x - footerBox.x)).toBeLessThan(40);
	expect(Math.abs(footerBox.x + footerBox.width - (counterBox.x + counterBox.width))).toBeLessThan(40);

	await page.getByRole('button', { name: 'Comic settings' }).click();
	const dialog = page.getByRole('dialog', { name: 'Comic settings' });
	await dialog.getByRole('button', { name: 'Double' }).click();
	// Cover alone, then 2-3, then page 4 alone (page 5 is wide), then page 5.
	// The counter is raw page numbers: 1, 3, 4, 5 out of 5 images.
	await expect(counter).toHaveText('1 / 5');
	await expect(images).toHaveCount(1);

	// Arrow keys turn pages anywhere in the reader and hide the header.
	await page.locator('.comic .counter').click();
	await page.keyboard.press('ArrowRight');
	await expect(counter).toHaveText('3 / 5');
	await expect(images).toHaveCount(2);
	// The two pages meet flush in the middle of the spread.
	const left = (await images.nth(0).boundingBox())!;
	const right = (await images.nth(1).boundingBox())!;
	expect(Math.abs(right.x - (left.x + left.width))).toBeLessThan(2);
	await expect(page.locator('.reader-header')).toBeHidden();

	// A wide page is shown alone; its partner is not skipped.
	await page.keyboard.press('ArrowRight');
	await expect(counter).toHaveText('4 / 5');
	await expect(images).toHaveCount(1);
	await page.keyboard.press('ArrowRight');
	await expect(counter).toHaveText('5 / 5');
	await expect(images).toHaveCount(1);

	// RTL reverses the physical page movement.
	await showHeader(page);
	await page.getByRole('button', { name: 'Comic settings' }).click();
	await dialog.getByRole('button', { name: 'R → L' }).click();
	await page.locator('.comic .counter').click();
	await page.keyboard.press('ArrowRight');
	await expect(counter).toHaveText('4 / 5');
});
