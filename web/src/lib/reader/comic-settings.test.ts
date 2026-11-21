import assert from 'node:assert/strict';
import test from 'node:test';
import {
	DEFAULT_COMIC_SETTINGS,
	loadComicSettings,
	saveComicSettings
} from './comic-settings.ts';

const store = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', {
	configurable: true,
	value: {
		getItem: (key: string) => store.get(key) ?? null,
		setItem: (key: string, value: string) => void store.set(key, value),
		removeItem: (key: string) => void store.delete(key)
	}
});

test('comic settings default when nothing is stored', () => {
	store.clear();
	assert.deepEqual(loadComicSettings(), DEFAULT_COMIC_SETTINGS);
});

test('comic settings survive a save/load round trip', () => {
	store.clear();
	saveComicSettings({ direction: 'rtl', pageMode: 'double' });
	assert.deepEqual(loadComicSettings(), { direction: 'rtl', pageMode: 'double' });
});

test('invalid comic settings fall back to defaults', () => {
	store.set('hearth:cbz-reader-settings', JSON.stringify({ direction: 'vertical', pageMode: 'wide' }));
	assert.deepEqual(loadComicSettings(), DEFAULT_COMIC_SETTINGS);
});
