import assert from 'node:assert/strict';
import { test } from 'node:test';
import { formatRuntime } from './runtime.ts';

test('formatRuntime omits the hour part for sub-hour runtimes', () => {
	assert.equal(formatRuntime(24 * 60), '24m');
	assert.equal(formatRuntime(90), '2m');
});

test('formatRuntime keeps hours and minutes for longer runtimes', () => {
	assert.equal(formatRuntime(2 * 3600 + 4 * 60), '2h 4m');
	assert.equal(formatRuntime(2 * 3600), '2h');
});

test('formatRuntime returns null for missing or invalid runtimes', () => {
	assert.equal(formatRuntime(0), null);
	assert.equal(formatRuntime(null), null);
	assert.equal(formatRuntime(undefined), null);
});
