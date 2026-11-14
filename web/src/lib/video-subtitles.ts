/**
 * Subtitle format conversion helpers for ASS/SSA and WebVTT playback.
 */

export function toAssTimestamp(ts: string): string {
	const parts = ts.trim().replace(',', '.').split(':');
	let h = 0;
	let m = 0;
	let s = 0;
	let ms = 0;
	if (parts.length === 3) {
		h = Number.parseInt(parts[0], 10) || 0;
		m = Number.parseInt(parts[1], 10) || 0;
		const secParts = parts[2].split('.');
		s = Number.parseInt(secParts[0], 10) || 0;
		ms = Number.parseInt(secParts[1] ?? '0', 10) || 0;
	} else if (parts.length === 2) {
		m = Number.parseInt(parts[0], 10) || 0;
		const secParts = parts[1].split('.');
		s = Number.parseInt(secParts[0], 10) || 0;
		ms = Number.parseInt(secParts[1] ?? '0', 10) || 0;
	}
	const cs = Math.floor(ms / 10)
		.toString()
		.padStart(2, '0')
		.slice(0, 2);
	return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${cs}`;
}

export function toAss(content: string): string {
	if (/(?:\[script info\]|\[events\]|\[v4\+? styles\])/i.test(content)) {
		return content;
	}
	const header = [
		'[Script Info]',
		'ScriptType: v4.00+',
		'WrapStyle: 0',
		'ScaledBorderAndShadow: yes',
		'YCbCr Matrix: None',
		'',
		'[V4+ Styles]',
		'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
		'Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1',
		'',
		'[Events]',
		'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text'
	].join('\n');

	const lines = content.replace(/\r\n/g, '\n').replace(/\r/g, '\n').split('\n');
	const dialogues: string[] = [];
	const timeRegex =
		/((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{2,3})\s*-->\s*((?:\d{1,2}:)?\d{2}:\d{2}[.,]\d{2,3})/;

	let i = 0;
	while (i < lines.length) {
		const line = lines[i].trim();
		const match = timeRegex.exec(line);
		if (match) {
			const start = toAssTimestamp(match[1]);
			const end = toAssTimestamp(match[2]);
			i += 1;
			const textLines: string[] = [];
			while (i < lines.length && lines[i].trim() !== '') {
				textLines.push(lines[i].trim());
				i += 1;
			}
			if (textLines.length > 0) {
				const text = textLines.join('\\N');
				dialogues.push(`Dialogue: 0,${start},${end},Default,,0,0,0,,${text}`);
			}
		} else {
			i += 1;
		}
	}
	return `${header}\n${dialogues.join('\n')}\n`;
}
