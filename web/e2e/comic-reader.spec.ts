/**
 * Comic reader footer layout, page modes, reading direction and keyboard
 * page turns.
 */
import { expect, test } from '@playwright/test';
import { seedServer, signIn } from './helpers';

async function openComic(page: import('@playwright/test').Page): Promise<void> {
	await page.goto('/books');
	await page.getByRole('link', { name: 'Sample Comic' }).first().click();
	await page.getByRole('link', { name: /^(Read|Resume) CBZ$/ }).first().click();
	await expect(page.getByRole('button', { name: 'Previous page' })).toBeVisible();
}

test('comic reader: footer edges, spreads, direction and arrow keys', async ({ page, request }) => {
	const pair = await seedServer(request);
	await signIn(page, pair);
	await openComic(page);

	const footer = page.locator('.comic .controls');
	const percentage = footer.locator('.percentage');
	const counter = footer.locator('.counter');
	await expect(percentage).toHaveText('25%');
	await expect(counter).toHaveText('1 / 4');

	// The footer spans the viewport so progress sits at the outer edges.
	const footerBox = (await footer.boundingBox())!;
	const percentBox = (await percentage.boundingBox())!;
	const counterBox = (await counter.boundingBox())!;
	expect(Math.abs(percentBox.x - footerBox.x)).toBeLessThan(40);
	expect(Math.abs(footerBox.x + footerBox.width - (counterBox.x + counterBox.width))).toBeLessThan(40);

	await page.getByRole('button', { name: 'Comic settings' }).click();
	const dialog = page.getByRole('dialog', { name: 'Comic settings' });
	await dialog.getByRole('button', { name: 'Double' }).click();
	await expect(counter).toHaveText('1 / 2');
	await expect(page.locator('.comic .pages img')).toHaveCount(2);

	// Arrow keys turn pages anywhere in the reader and hide the header.
	await page.locator('.comic .counter').click();
	await page.keyboard.press('ArrowRight');
	await expect(counter).toHaveText('2 / 2');
	await expect(page.locator('.reader-header')).toBeHidden();

	// RTL reverses the physical page movement. Middle-tap restores the header.
	const area = (await page.locator('.comic .page-area').boundingBox())!;
	await page.mouse.click(area.x + area.width / 2, area.y + area.height / 2);
	await expect(page.locator('.reader-header')).toBeVisible();
	await page.getByRole('button', { name: 'Comic settings' }).click();
	await dialog.getByRole('button', { name: 'R → L' }).click();
	await page.locator('.comic .counter').click();
	await page.keyboard.press('ArrowRight');
	await expect(counter).toHaveText('1 / 2');
});
