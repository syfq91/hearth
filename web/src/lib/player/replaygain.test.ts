import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
	REPLAY_GAIN_STORAGE_KEY,
	readReplayGainPreference,
	replayGainLinear,
	writeReplayGainPreference
} from './replaygain.ts';

test('no metadata leaves the volume untouched', () => {
	assert.equal(replayGainLinear(null), 1);
	assert.equal(replayGainLinear({}), 1);
	assert.equal(replayGainLinear({ replayGainTrackGain: null }), 1);
	assert.equal(replayGainLinear({ replayGainTrackGain: Number.NaN }), 1);
});

test('negative gain attenuates and positive gain boosts', () => {
	assert.ok(Math.abs(replayGainLinear({ replayGainTrackGain: -6 }) - 0.5012) < 0.001);
	assert.ok(Math.abs(replayGainLinear({ replayGainTrackGain: 6 }) - 1.9953) < 0.001);
});

test('album gain is preferred with its matching peak', () => {
	const gain = replayGainLinear({
		replayGainTrackGain: -12,
		replayGainTrackPeak: 0.5,
		replayGainAlbumGain: -3,
		replayGainAlbumPeak: 0.8
	});
	assert.ok(Math.abs(gain - 0.7079) < 0.001);
});

test('track gain and peak are used when album gain is missing', () => {
	const gain = replayGainLinear({
		replayGainTrackGain: -3,
		replayGainTrackPeak: 0.8,
		replayGainAlbumPeak: 0.1
	});
	assert.ok(Math.abs(gain - 0.7079) < 0.001);
});

test('peak limits a boost so the signal cannot clip', () => {
	// +10 dB is ~3.16 linear, but a 0.9 peak may only be boosted by ~1.11.
	const gain = replayGainLinear({ replayGainTrackGain: 10, replayGainTrackPeak: 0.9 });
	assert.ok(Math.abs(gain - 1 / 0.9) < 0.001);
});

test('boost is capped even without a peak', () => {
	assert.equal(replayGainLinear({ replayGainTrackGain: 40 }), 4);
	assert.equal(replayGainLinear({ replayGainTrackGain: 20, replayGainTrackPeak: 0 }), 4);
});

function fakeStorage() {
	const values = new Map<string, string>();
	return {
		values,
		getItem: (key: string) => values.get(key) ?? null,
		setItem: (key: string, value: string) => void values.set(key, value)
	};
}

test('ReplayGain is off unless explicitly enabled', () => {
	assert.equal(readReplayGainPreference(null), false);
	assert.equal(readReplayGainPreference(fakeStorage()), false);
});

test('the ReplayGain preference round-trips through storage', () => {
	const storage = fakeStorage();
	writeReplayGainPreference(storage, true);
	assert.equal(storage.values.get(REPLAY_GAIN_STORAGE_KEY), '1');
	assert.equal(readReplayGainPreference(storage), true);

	writeReplayGainPreference(storage, false);
	assert.equal(storage.values.get(REPLAY_GAIN_STORAGE_KEY), '0');
	assert.equal(readReplayGainPreference(storage), false);
});

test('unreadable storage never enables ReplayGain', () => {
	const blocked = {
		getItem: () => {
			throw new Error('blocked');
		},
		setItem: () => {
			throw new Error('blocked');
		}
	};
	assert.equal(readReplayGainPreference(blocked), false);
	assert.doesNotThrow(() => writeReplayGainPreference(blocked, true));
});
