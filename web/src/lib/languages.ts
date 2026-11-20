export interface LanguageOption {
	code: string;
	label: string;
}

export const COMMON_LANGUAGES: LanguageOption[] = [
	{ code: 'en', label: 'English' },
	{ code: 'ja', label: 'Japanese' },
	{ code: 'es', label: 'Spanish' },
	{ code: 'fr', label: 'French' },
	{ code: 'de', label: 'German' },
	{ code: 'it', label: 'Italian' },
	{ code: 'pt', label: 'Portuguese' },
	{ code: 'zh', label: 'Chinese' },
	{ code: 'ko', label: 'Korean' },
	{ code: 'ru', label: 'Russian' },
	{ code: 'ar', label: 'Arabic' },
	{ code: 'hi', label: 'Hindi' },
	{ code: 'nl', label: 'Dutch' },
	{ code: 'pl', label: 'Polish' },
	{ code: 'sv', label: 'Swedish' },
	{ code: 'tr', label: 'Turkish' },
	{ code: 'id', label: 'Indonesian' },
	{ code: 'vi', label: 'Vietnamese' },
	{ code: 'th', label: 'Thai' }
];

const COMMON_LANGUAGE_ALIASES: Record<string, string[]> = {
	en: ['eng', 'english'],
	ja: ['jpn', 'japanese'],
	es: ['spa', 'spanish', 'espanol', 'español'],
	fr: ['fra', 'fre', 'french', 'francais', 'français'],
	de: ['deu', 'ger', 'german', 'deutsch'],
	it: ['ita', 'italian', 'italiano'],
	pt: ['por', 'portuguese', 'português'],
	zh: ['zho', 'chi', 'chinese'],
	ko: ['kor', 'korean'],
	ru: ['rus', 'russian'],
	ar: ['ara', 'arabic'],
	hi: ['hin', 'hindi'],
	nl: ['nld', 'dut', 'dutch'],
	pl: ['pol', 'polish'],
	sv: ['swe', 'swedish'],
	tr: ['tur', 'turkish'],
	id: ['ind', 'indonesian'],
	vi: ['vie', 'vietnamese'],
	th: ['tha', 'thai']
};

/** Display name for an ISO 639-1/2 track language, falling back to the code. */
export function languageLabel(code: string | null | undefined): string | null {
	if (!code?.trim()) return null;
	const normalized = code.trim().toLowerCase();
	for (const { code: short, label } of COMMON_LANGUAGES) {
		if (normalized === short || COMMON_LANGUAGE_ALIASES[short]?.includes(normalized)) {
			return label;
		}
	}
	return code.trim().toUpperCase();
}

function hasWord(text: string, word: string): boolean {
	if (!text || !word) return false;
	const regex = new RegExp(`(^|[^a-z0-9])${word}([^a-z0-9]|$)`, 'i');
	return regex.test(text);
}

export function matchesLanguage(
	trackLang: string | null | undefined,
	trackTitle: string | null | undefined,
	preferred: string | null | undefined
): boolean {
	if (!preferred) return false;
	const pref = preferred.trim().toLowerCase();
	if (!pref) return false;
	const lang = (trackLang ?? '').trim().toLowerCase();
	const title = (trackTitle ?? '').trim().toLowerCase();
	if (lang === pref) return true;
	if (pref.length === 2 && lang.startsWith(pref)) return true;
	if (lang.length === 2 && pref.startsWith(lang)) return true;

	for (const [code, aliases] of Object.entries(COMMON_LANGUAGE_ALIASES)) {
		const group = [code, ...aliases];
		if (group.some((a) => pref === a)) {
			if (group.some((a) => lang === a || hasWord(title, a))) return true;
		}
	}
	return hasWord(title, pref);
}

export function findMatchingSubtitleTrack(
	subtitles: Array<{ id: string; name?: string | null; language?: string | null }> | null | undefined,
	embeddedSubtitles: Array<{ index: number; title?: string | null; language?: string | null }> | null | undefined,
	preferredLang: string | null | undefined
): string | null {
	if (!preferredLang) return null;
	if (subtitles) {
		const match = subtitles.find((t) => matchesLanguage(t.language, t.name, preferredLang));
		if (match) return `ext:${match.id}`;
	}
	if (embeddedSubtitles) {
		const match = embeddedSubtitles.find((s) => matchesLanguage(s.language, s.title, preferredLang));
		if (match) return `emb:${match.index}`;
	}
	return null;
}

export interface AutoSubtitleOptions {
	subtitles?: Array<{ id: string; name?: string | null; language?: string | null }> | null;
	embeddedSubtitles?: Array<{ index: number; title?: string | null; language?: string | null }> | null;
	activeAudio?: { language?: string | null; title?: string | null } | null;
	preferredAudioLanguage?: string | null;
	preferredSubtitleLanguage?: string | null;
	subtitleMode?: 'always' | 'never' | 'smart' | string | null;
}

export function resolveAutoSubtitle(opts: AutoSubtitleOptions): string | null {
	const mode = opts.subtitleMode ?? 'smart';
	if (mode === 'never') return null;

	const allSubs = opts.subtitles ?? [];
	const allEmb = opts.embeddedSubtitles ?? [];
	if (allSubs.length === 0 && allEmb.length === 0) return null;

	// Check forced track first
	const forcedExternal = allSubs.find((t) => (t.name ?? '').toLowerCase().includes('forced'));
	if (forcedExternal) return `ext:${forcedExternal.id}`;
	const forcedEmbedded = allEmb.find((s) => (s.title ?? '').toLowerCase().includes('forced'));
	if (forcedEmbedded) return `emb:${forcedEmbedded.index}`;

	if (mode === 'always') {
		const match = findMatchingSubtitleTrack(allSubs, allEmb, opts.preferredSubtitleLanguage);
		if (match) return match;
		if (allSubs.length > 0) return `ext:${allSubs[0].id}`;
		if (allEmb.length > 0) return `emb:${allEmb[0].index}`;
		return null;
	}

	if (mode === 'smart' && opts.preferredAudioLanguage) {
		const matches = matchesLanguage(
			opts.activeAudio?.language,
			opts.activeAudio?.title,
			opts.preferredAudioLanguage
		);
		if (!matches) {
			const match = findMatchingSubtitleTrack(
				allSubs,
				allEmb,
				opts.preferredSubtitleLanguage ?? opts.preferredAudioLanguage
			);
			if (match) return match;
			if (allSubs.length > 0) return `ext:${allSubs[0].id}`;
			if (allEmb.length > 0) return `emb:${allEmb[0].index}`;
		}
	}
	return null;
}
