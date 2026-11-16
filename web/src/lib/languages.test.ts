import test from 'node:test';
import assert from 'node:assert/strict';
import { findMatchingSubtitleTrack, matchesLanguage } from './languages.ts';

test('matchesLanguage matches exact and 2-to-3 letter codes', () => {
	assert.equal(matchesLanguage('en', 'Audio', 'en'), true);
	assert.equal(matchesLanguage('eng', 'Audio', 'en'), true);
	assert.equal(matchesLanguage('en', 'Audio', 'eng'), true);
	assert.equal(matchesLanguage('ja', 'Audio', 'jpn'), true);
	assert.equal(matchesLanguage('jpn', 'Audio', 'ja'), true);
	assert.equal(matchesLanguage('spa', 'Audio', 'es'), true);
});

test('matchesLanguage matches by title', () => {
	assert.equal(matchesLanguage(undefined, 'English Commentary', 'en'), true);
	assert.equal(matchesLanguage(undefined, 'Japanese (Original)', 'ja'), true);
});

test('matchesLanguage returns false when no match', () => {
	assert.equal(matchesLanguage('fr', 'French', 'en'), false);
	assert.equal(matchesLanguage(undefined, undefined, 'en'), false);
	assert.equal(matchesLanguage('en', 'English', undefined), false);
});

test('findMatchingSubtitleTrack finds external or embedded match', () => {
	const subs = [{ id: 'sub1', name: 'English', language: 'en' }];
	const emb = [{ index: 2, title: 'Japanese', language: 'ja' }];
	assert.equal(findMatchingSubtitleTrack(subs, emb, 'en'), 'ext:sub1');
	assert.equal(findMatchingSubtitleTrack(subs, emb, 'ja'), 'emb:2');
	assert.equal(findMatchingSubtitleTrack(subs, emb, 'de'), null);
	assert.equal(findMatchingSubtitleTrack(subs, emb, undefined), null);
});
