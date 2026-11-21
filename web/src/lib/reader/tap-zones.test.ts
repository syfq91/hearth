import assert from 'node:assert/strict';
import test from 'node:test';
import { isTap, tapZoneAt } from './tap-zones.ts';

const rect = (left = 0, width = 300): DOMRect =>
	({ left, width }) as DOMRect;

test('tap zones split the reading area into thirds', () => {
	assert.equal(tapZoneAt(50, rect()), 'prev');
	assert.equal(tapZoneAt(99, rect()), 'prev');
	assert.equal(tapZoneAt(100, rect()), 'toggle');
	assert.equal(tapZoneAt(200, rect()), 'toggle');
	assert.equal(tapZoneAt(201, rect()), 'next');
	assert.equal(tapZoneAt(280, rect()), 'next');
});

test('tap zones account for the element offset', () => {
	assert.equal(tapZoneAt(150, rect(100, 300)), 'prev');
	assert.equal(tapZoneAt(250, rect(100, 300)), 'toggle');
	assert.equal(tapZoneAt(350, rect(100, 300)), 'next');
});

test('isTap ignores pointer drift', () => {
	assert.equal(isTap({ x: 100, y: 100 }, { x: 105, y: 103 }), true);
	assert.equal(isTap({ x: 100, y: 100 }, { x: 130, y: 100 }), false);
	assert.equal(isTap({ x: 100, y: 100 }, { x: 100, y: 130 }), false);
});
