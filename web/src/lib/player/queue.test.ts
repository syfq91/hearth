import { test } from 'node:test';
import assert from 'node:assert/strict';
import { REPEAT_MODES, indexAfterRemoval, nextIndex, shuffledUpcoming } from './queue.ts';

test('repeat modes cycle off, all, one', () => {
	assert.deepEqual(REPEAT_MODES, ['off', 'all', 'one']);
});

test('nextIndex advances through the queue', () => {
	assert.equal(nextIndex(0, 3, false), 1);
	assert.equal(nextIndex(2, 3, false), -1);
	assert.equal(nextIndex(2, 3, true), 0);
	assert.equal(nextIndex(-1, 3, true), -1);
	assert.equal(nextIndex(0, 0, true), -1);
});

test('indexAfterRemoval shifts only when the removed track was before the current one', () => {
	assert.equal(indexAfterRemoval(0, 3), 2);
	assert.equal(indexAfterRemoval(2, 2), 2);
	assert.equal(indexAfterRemoval(4, 2), 2);
});

test('shuffledUpcoming keeps the played prefix and current track in place', () => {
	const items = ['a', 'b', 'c', 'd', 'e'];
	// A deterministic "random" that always selects index 0.
	const result = shuffledUpcoming(items, 1, () => 0);
	assert.deepEqual(result.slice(0, 2), ['a', 'b']);
	assert.deepEqual([...result.slice(2)].sort(), ['c', 'd', 'e']);
	assert.equal(result.length, items.length);
});

test('shuffledUpcoming handles empty and boundary tails', () => {
	assert.deepEqual(shuffledUpcoming(['a'], 0), ['a']);
	assert.deepEqual(shuffledUpcoming([], -1), []);
});
