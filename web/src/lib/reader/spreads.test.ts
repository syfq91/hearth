import assert from 'node:assert/strict';
import test from 'node:test';
import { singleSpread, spreadAt, spreadCount, spreadIndex, spreadStartFor } from './spreads.ts';

test('the cover is shown alone and the rest pair up', () => {
	assert.deepEqual(spreadAt(1, 6).pages, [1]);
	assert.deepEqual(spreadAt(2, 6).pages, [2, 3]);
	assert.deepEqual(spreadAt(4, 6).pages, [4, 5]);
	assert.deepEqual(spreadAt(6, 6).pages, [6]);
});

test('spread navigation stops at the ends and skips the cover', () => {
	assert.deepEqual(spreadAt(1, 6), { pages: [1], next: 2, prev: 1 });
	assert.deepEqual(spreadAt(2, 6), { pages: [2, 3], next: 4, prev: 1 });
	assert.deepEqual(spreadAt(4, 6), { pages: [4, 5], next: 6, prev: 2 });
	assert.deepEqual(spreadAt(6, 6), { pages: [6], next: 6, prev: 4 });
});

test('spread counts and indices include the cover as one view', () => {
	assert.equal(spreadCount(0), 0);
	assert.equal(spreadCount(1), 1);
	assert.equal(spreadCount(4), 3); // cover, 2-3, 4
	assert.equal(spreadCount(5), 3); // cover, 2-3, 4-5
	assert.equal(spreadIndex(1, 5), 1);
	assert.equal(spreadIndex(2, 5), 2);
	assert.equal(spreadIndex(5, 5), 3);
});

test('spreadStartFor maps any page back to its view start', () => {
	assert.equal(spreadStartFor(1, 6), 1);
	assert.equal(spreadStartFor(3, 6), 2);
	assert.equal(spreadStartFor(5, 6), 4);
	assert.equal(spreadStartFor(6, 6), 6);
});

test('single page mode keeps its own navigation', () => {
	assert.deepEqual(singleSpread(1, 4), { pages: [1], next: 2, prev: 1 });
	assert.deepEqual(singleSpread(4, 4), { pages: [4], next: 4, prev: 3 });
});
