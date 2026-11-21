/** Reader display preferences, persisted locally per browser. */

export interface ReaderSettings {
	/** Base text size in pixels. */
	fontSize: number;
	fontFamily: 'serif' | 'sans' | 'mono';
	theme: 'light' | 'sepia' | 'dark';
}

export const FONT_SIZES = [14, 18, 22] as const;
export const FONT_FAMILIES: ReaderSettings['fontFamily'][] = ['serif', 'sans', 'mono'];
export const THEMES: ReaderSettings['theme'][] = ['light', 'sepia', 'dark'];

export const DEFAULT_READER_SETTINGS: ReaderSettings = {
	fontSize: 18,
	fontFamily: 'serif',
	theme: 'dark'
};

const STORAGE_KEY = 'hearth:epub-reader-settings';

export function loadReaderSettings(): ReaderSettings {
	if (typeof localStorage === 'undefined') return { ...DEFAULT_READER_SETTINGS };
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return { ...DEFAULT_READER_SETTINGS };
		const parsed = JSON.parse(raw) as Partial<ReaderSettings>;
		return {
			fontSize: FONT_SIZES.includes(parsed.fontSize as (typeof FONT_SIZES)[number])
				? (parsed.fontSize as number)
				: DEFAULT_READER_SETTINGS.fontSize,
			fontFamily: FONT_FAMILIES.includes(parsed.fontFamily as ReaderSettings['fontFamily'])
				? (parsed.fontFamily as ReaderSettings['fontFamily'])
				: DEFAULT_READER_SETTINGS.fontFamily,
			theme: THEMES.includes(parsed.theme as ReaderSettings['theme'])
				? (parsed.theme as ReaderSettings['theme'])
				: DEFAULT_READER_SETTINGS.theme
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
