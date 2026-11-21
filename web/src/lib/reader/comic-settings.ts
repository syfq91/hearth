/** Comic reader display preferences, persisted locally per browser. */

export interface ComicSettings {
	/** Physical page order: left-to-right (Western) or right-to-left (manga). */
	direction: 'ltr' | 'rtl';
	/** Show one page per view, or two pages side by side. */
	pageMode: 'single' | 'double';
}

export const DEFAULT_COMIC_SETTINGS: ComicSettings = {
	direction: 'ltr',
	pageMode: 'single'
};

const STORAGE_KEY = 'hearth:cbz-reader-settings';

export function loadComicSettings(): ComicSettings {
	if (typeof localStorage === 'undefined') return { ...DEFAULT_COMIC_SETTINGS };
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (!raw) return { ...DEFAULT_COMIC_SETTINGS };
		const parsed = JSON.parse(raw) as Partial<ComicSettings>;
		return {
			direction: parsed.direction === 'rtl' ? 'rtl' : 'ltr',
			pageMode: parsed.pageMode === 'double' ? 'double' : 'single'
		};
	} catch {
		return { ...DEFAULT_COMIC_SETTINGS };
	}
}

export function saveComicSettings(settings: ComicSettings): void {
	if (typeof localStorage === 'undefined') return;
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
	} catch {
		// Storage may be unavailable (private mode, quota); settings still
		// apply for the current session.
	}
}
