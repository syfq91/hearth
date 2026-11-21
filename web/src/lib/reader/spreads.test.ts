import assert from 'node:assert/strict';
import test from 'node:test';
import {
	isWidePage,
	singleSpread,
	spreadAt,
	spreadCount,
	spreadIndex,
	spreadStartFor,
	type PageDimensions
} from './spreads.ts';

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

test('wide images are identified by aspect ratio', () => {
	assert.equal(isWidePage({ width: 1600, height: 1000 }), true);
	assert.equal(isWidePage({ width: 800, height: 1200 }), false);
	assert.equal(isWidePage({ width: 1200, height: 1000 }), false);
	assert.equal(isWidePage(undefined), false);
});

test('a wide page is shown alone and pairing resumes after it', () => {
	const dimensions: Record<number, PageDimensions> = {
		3: { width: 1600, height: 1000 }
	};
	assert.deepEqual(spreadAt(2, 6, dimensions).pages, [2]);
	assert.deepEqual(spreadAt(3, 6, dimensions), { pages: [3], next: 4, prev: 2 });
	assert.deepEqual(spreadAt(4, 6, dimensions).pages, [4, 5]);
	assert.equal(spreadCount(6, dimensions), 5); // 1, 2, 3, 4-5, 6
	assert.equal(spreadIndex(4, 6, dimensions), 4);
});

test('a wide page at the end of a pair does not skip it', () => {
	const dimensions: Record<number, PageDimensions> = {
		5: { width: 1800, height: 1000 }
	};
	assert.deepEqual(spreadAt(4, 6, dimensions), { pages: [4], next: 5, prev: 2 });
});
