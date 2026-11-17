import assert from 'node:assert/strict';
import test from 'node:test';

import { pageWindow } from './page-window.ts';

test('pageWindow clamps to document bounds', () => {
	assert.deepEqual(pageWindow(1, 10), { first: 1, last: 3 });
	assert.deepEqual(pageWindow(10, 10), { first: 9, last: 10 });
});

test('pageWindow follows the current page', () => {
	assert.deepEqual(pageWindow(5, 10), { first: 4, last: 7 });
	assert.deepEqual(pageWindow(0, 10), { first: 1, last: 3 });
	assert.deepEqual(pageWindow(99, 10), { first: 9, last: 10 });
});

test('pageWindow handles empty documents', () => {
	assert.deepEqual(pageWindow(1, 0), { first: 1, last: 0 });
});
