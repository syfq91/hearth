import assert from 'node:assert/strict';
import test from 'node:test';
import { DEFAULT_READER_SETTINGS, loadReaderSettings, saveReaderSettings } from './settings.ts';

const store = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', {
	configurable: true,
	value: {
		getItem: (key: string) => store.get(key) ?? null,
		setItem: (key: string, value: string) => void store.set(key, value),
		removeItem: (key: string) => void store.delete(key)
	}
});

test('settings default when nothing is stored', () => {
	store.clear();
	assert.deepEqual(loadReaderSettings(), DEFAULT_READER_SETTINGS);
});

test('settings survive a save/load round trip', () => {
	store.clear();
	saveReaderSettings({ fontSize: 22, fontFamily: 'sans', theme: 'sepia' });
	assert.deepEqual(loadReaderSettings(), { fontSize: 22, fontFamily: 'sans', theme: 'sepia' });
});

test('corrupt stored settings fall back to defaults', () => {
	store.set('hearth:epub-reader-settings', '{not json');
	assert.deepEqual(loadReaderSettings(), DEFAULT_READER_SETTINGS);

	store.set(
		'hearth:epub-reader-settings',
		JSON.stringify({ fontSize: 999, fontFamily: 'comic', theme: 'neon' })
	);
	assert.deepEqual(loadReaderSettings(), {
		fontSize: 32,
		fontFamily: 'serif',
		theme: 'dark'
	});
});
