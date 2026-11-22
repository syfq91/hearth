import assert from 'node:assert/strict';
import test from 'node:test';

import { CHROME_HIDE_MS, createAutoHideChrome } from './auto-hide.ts';

const DELAY = 30;
const settle = () => new Promise((resolve) => setTimeout(resolve, DELAY * 3));

function harness(persist: () => boolean = () => false) {
	const changes: boolean[] = [];
	const chrome = createAutoHideChrome({
		hideDelayMs: DELAY,
		persist,
		onChange: (visible) => changes.push(visible)
	});
	return { chrome, changes };
}

test('exposes a single shared hide delay', () => {
	assert.equal(CHROME_HIDE_MS, 2500);
});

test('reveals on activity, then hides once the delay elapses', async () => {
	const { chrome, changes } = harness();
	assert.equal(chrome.visible, true);

	chrome.reveal();
	assert.equal(chrome.visible, true);
	await settle();

	assert.equal(chrome.visible, false);
	assert.deepEqual(changes, [false]);
});

test('keeps re-arming while persist() is true, and hides once it clears', async () => {
	let busy = true;
	const { chrome } = harness(() => busy);

	chrome.reveal();
	await settle();
	assert.equal(chrome.visible, true, 'an open menu must not lose its chrome');

	busy = false;
	await settle();
	assert.equal(chrome.visible, false);
});

test('an explicit hide cancels a pending timer', async () => {
	const { chrome } = harness();

	chrome.reveal();
	chrome.hide();
	await settle();

	assert.equal(chrome.visible, false);
});

test('toggle alternates and each reveal restarts the timer', async () => {
	const { chrome } = harness();

	chrome.toggle();
	assert.equal(chrome.visible, false);
	await settle();
	assert.equal(chrome.visible, false);

	chrome.toggle();
	assert.equal(chrome.visible, true);
	await settle();
	assert.equal(chrome.visible, false);

	chrome.dispose();
});

test('each reveal restarts the countdown', async () => {
	const changes: boolean[] = [];
	const chrome = createAutoHideChrome({
		hideDelayMs: 60,
		persist: () => false,
		onChange: (visible) => changes.push(visible)
	});

	chrome.reveal();
	await delay(40);
	assert.equal(chrome.visible, true);
	chrome.reveal();
	await delay(40);
	assert.equal(chrome.visible, true, 'the second reveal restarted the countdown');
	await delay(30);
	assert.equal(chrome.visible, false);
	chrome.dispose();
});

test('dispose stops the timer', async () => {
	const { chrome } = harness();

	chrome.reveal();
	chrome.dispose();
	await settle();

	assert.equal(chrome.visible, true);
});

function delay(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}
