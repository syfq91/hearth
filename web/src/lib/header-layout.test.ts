import assert from 'node:assert/strict';
import test from 'node:test';

import { initialHeaderLayout, nextHeaderLayout } from './header-layout.ts';

test('narrowing falls back from individual links to grouped, then to icons', () => {
	let layout = initialHeaderLayout();
	assert.equal(nextHeaderLayout(layout, 1200, false), layout);

	layout = nextHeaderLayout(layout, 900, true);
	assert.equal(layout.combined, true);
	assert.equal(layout.compact, false);

	layout = nextHeaderLayout(layout, 640, true);
	assert.equal(layout.combined, true);
	assert.equal(layout.compact, true);
});

test('a still-overflowing compact nav raises its threshold without oscillating', () => {
	let layout = { ...initialHeaderLayout(), combined: true, compact: true };
	layout = nextHeaderLayout(layout, 600, true);
	assert.equal(layout.minCompactWidth, 620);

	const same = nextHeaderLayout(layout, 600, true);
	assert.equal(same.compact, true);
	assert.equal(same.minCompactWidth, 620);
});

test('growing restores labels and then individual links with hysteresis', () => {
	let layout = { ...initialHeaderLayout(), combined: true, compact: true, minCompactWidth: 620 };

	assert.equal(nextHeaderLayout(layout, 630, false), layout);
	layout = nextHeaderLayout(layout, 635, false);
	assert.equal(layout.compact, false);

	layout = { ...layout, minUncombinedWidth: 900 };
	assert.equal(nextHeaderLayout(layout, 910, false), layout);
	layout = nextHeaderLayout(layout, 915, false);
	assert.equal(layout.combined, false);
});

test('a wider mode that still overflows raises its threshold', () => {
	let layout = nextHeaderLayout(initialHeaderLayout(), 700, true);
	layout = nextHeaderLayout(layout, 735, false);
	assert.equal(layout.combined, false);

	layout = nextHeaderLayout(layout, 735, true);
	assert.equal(layout.combined, true);
	assert.equal(layout.minUncombinedWidth, 755);
});

test('compact mode always implies grouped mode', () => {
	let layout = initialHeaderLayout();
	for (const width of [1200, 900, 700, 640, 600, 500]) {
		for (const overflow of [false, true, false, true]) {
			layout = nextHeaderLayout(layout, width, overflow);
			if (layout.compact) assert.equal(layout.combined, true);
		}
	}
});
