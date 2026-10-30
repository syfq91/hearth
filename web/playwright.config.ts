import { defineConfig } from '@playwright/test';

/**
 * The suite runs against a live Hearth container started by scripts/e2e.sh;
 * HEARTH_E2E_BASE_URL points at it. Locally you can also run it against
 * `docker compose up` or any dev server — just export the variable.
 */
const baseURL = process.env['HEARTH_E2E_BASE_URL'] ?? 'http://127.0.0.1:8734';

export default defineConfig({
	testDir: './e2e',
	// Seeding scans a real library on a real container, so a test can spend a
	// minute waiting for jobs before it asserts anything.
	timeout: 120_000,
	// The suite shares one server (and one user/database): serialise it.
	fullyParallel: false,
	workers: 1,
	forbidOnly: !!process.env['CI'],
	retries: process.env['CI'] ? 1 : 0,
	reporter: [['list'], ['html', { open: 'never' }]],
	use: {
		baseURL,
		// The full Chromium build ships H.264/AAC playback (the headless shell
		// does not decode those), which the video fixture needs.
		channel: 'chromium',
		trace: 'on-first-retry',
		screenshot: 'only-on-failure',
		// Autoplay without a user gesture so the <video> can start on its own.
		launchOptions: { args: ['--autoplay-policy=no-user-gesture-required'] }
	}
});
