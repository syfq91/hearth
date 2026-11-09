/**
 * Authentication: the anonymous guard, a failed sign-in, a successful one,
 * and signing out.
 */
import { expect, test } from '@playwright/test';
import { ADMIN_PASSWORD, ADMIN_USERNAME, ensureAccount } from './helpers';

test('anonymous visitors are sent to the login page', async ({ page }) => {
	await page.goto('/');
	await expect(page).toHaveURL(/\/login$/);
	await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
});

test('login page hides the admin setup link once an account exists', async ({ page, request }) => {
	await ensureAccount(request);

	await page.goto('/login');
	await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Set up the admin account' })).not.toBeVisible();
});

test('a wrong password is rejected with an error', async ({ page, request }) => {
	await ensureAccount(request);

	await page.goto('/login');
	await page.locator('input[name="username"]').fill(ADMIN_USERNAME);
	await page.locator('input[name="password"]').fill('definitely-not-the-password');
	await page.getByRole('button', { name: 'Sign in' }).click();

	await expect(page.getByRole('alert')).toBeVisible();
	await expect(page).toHaveURL(/\/login$/);
});

test('sign in and out through the UI', async ({ page, request }) => {
	await ensureAccount(request);

	await page.goto('/login');
	await page.locator('input[name="username"]').fill(ADMIN_USERNAME);
	await page.locator('input[name="password"]').fill(ADMIN_PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();

	await expect(page).toHaveURL(/\/$/);
	await expect(page.getByRole('heading', { name: 'Home', level: 1 })).toBeAttached();
	// The topbar shows the signed-in user with their role badge.
	await expect(page.getByRole('link', { name: new RegExp(ADMIN_USERNAME) })).toBeVisible();

	await page.getByRole('button', { name: 'Sign out' }).click();
	await expect(page).toHaveURL(/\/login$/);

	// The guard is back in force.
	await page.goto('/');
	await expect(page).toHaveURL(/\/login$/);
});
