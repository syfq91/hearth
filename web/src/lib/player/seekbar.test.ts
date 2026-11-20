import test from 'node:test';
import assert from 'node:assert';
import { contentDuration, contentTime } from './seekbar.ts';

test('contentTime subtracts the container presentation offset', () => {
	assert.strictEqual(contentTime(70, 10), 60);
	assert.strictEqual(contentTime(9.977, 9.977), 0);
});

test('contentTime clamps to zero and ignores negative offsets', () => {
	assert.strictEqual(contentTime(5, 10), 0);
	assert.strictEqual(contentTime(70, -5), 70);
});

test('contentDuration normalizes raw media duration, keeping bars on one scale', () => {
	// A container starting at ~10s whose raw end timestamp is ~130s has ~120s
	// of content. The played and buffered bars must both use 120 as denominator.
	const rawDuration = 130.023;
	const startOffset = 9.977;
	const duration = contentDuration(rawDuration, startOffset);
	assert.ok(Math.abs(duration - 120.046) < 0.001);

	const resumeRaw = 70;
	const bufferedEndRaw = 105;
	const playedPct = contentTime(resumeRaw, startOffset) / duration;
	const bufferedStartPct = contentTime(resumeRaw, startOffset) / duration;
	// The buffered overlay starts exactly where the played bar ends.
	assert.strictEqual(bufferedStartPct, playedPct);
	assert.ok(Math.abs(playedPct - 60.023 / duration) < 1e-9);
	assert.ok(Math.abs(contentTime(bufferedEndRaw, startOffset) / duration - 95.023 / duration) < 1e-9);
	// The buffered overlay ends at the real content end, not short of it.
	assert.ok(Math.abs(contentTime(rawDuration, startOffset) / duration - 1) < 1e-9);
});

test('contentDuration returns 0 for unknown or invalid durations', () => {
	assert.strictEqual(contentDuration(0, 10), 0);
	assert.strictEqual(contentDuration(-1, 10), 0);
	assert.strictEqual(contentDuration(Number.POSITIVE_INFINITY, 10), 0);
});
