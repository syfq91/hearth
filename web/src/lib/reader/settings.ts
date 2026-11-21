/** Reader display preferences, persisted locally per browser. */

export interface ReaderSettings {
	/** Base text size in pixels. */
	fontSize: number;
	fontFamily: 'serif' | 'sans' | 'mono';
	theme: 'light' | 'sepia' | 'dark';
	/** Text columns per screen (foliate's max-column-count). */
	columns: 1 | 2;
}

export const MIN_FONT_SIZE = 10;
export const MAX_FONT_SIZE = 32;
export const FONT_SIZE_STEP = 1;
export const FONT_FAMILIES: ReaderSettings['fontFamily'][] = ['serif', 'sans', 'mono'];
export const THEMES: ReaderSettings['theme'][] = ['light', 'sepia', 'dark'];

export const DEFAULT_READER_SETTINGS: ReaderSettings = {
	fontSize: 18,
	fontFamily: 'serif',
	theme: 'dark',
	columns: 1
};

const STORAGE_KEY = 'hearth:epub-reader-settings';

export function loadReaderSettings(): ReaderSettings {
	if (typeof localStorage === 'undefined') return { ...DEFAULT_READER_SETTINGS };
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return { ...DEFAULT_READER_SETTINGS };
		const parsed = JSON.parse(raw) as Partial<ReaderSettings>;
		const fontSize = Number(parsed.fontSize);
		return {
			fontSize: Number.isFinite(fontSize)
				? Math.min(Math.max(Math.round(fontSize), MIN_FONT_SIZE), MAX_FONT_SIZE)
				: DEFAULT_READER_SETTINGS.fontSize,
			fontFamily: FONT_FAMILIES.includes(parsed.fontFamily as ReaderSettings['fontFamily'])
				? (parsed.fontFamily as ReaderSettings['fontFamily'])
				: DEFAULT_READER_SETTINGS.fontFamily,
			theme: THEMES.includes(parsed.theme as ReaderSettings['theme'])
				? (parsed.theme as ReaderSettings['theme'])
				: DEFAULT_READER_SETTINGS.theme,
			columns: parsed.columns === 2 ? 2 : 1
		};
	} catch {
		return { ...DEFAULT_READER_SETTINGS };
	}
}

export function saveReaderSettings(settings: ReaderSettings): void {
	if (typeof localStorage === 'undefined') return;
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
	} catch {
		// Storage may be unavailable (private mode, quota); settings still
		// apply for the current session.
	}
}
