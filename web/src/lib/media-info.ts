/** Formatting for the video/audio/subtitle rows on item detail pages. */
import type { S } from './api/types';
import { languageLabel } from './languages.ts';

type ProbeStream = S['ProbeStream'];
type MediaFile = S['MediaFileResponse'];
type Subtitle = S['SubtitleResponse'];

export interface MediaTrack {
	value: string;
	label: string;
}

export interface MediaInfo {
	video: string | null;
	audio: MediaTrack[];
	subtitles: MediaTrack[];
}

const CODEC_LABELS: Record<string, string> = {
	h264: 'H.264',
	hevc: 'HEVC',
	av1: 'AV1',
	vp9: 'VP9',
	mpeg4: 'MPEG-4',
	aac: 'AAC',
	flac: 'FLAC',
	opus: 'Opus',
	ac3: 'Dolby Digital',
	eac3: 'Dolby Digital Plus',
	truehd: 'Dolby TrueHD',
	dts: 'DTS',
	ass: 'ASS',
	ssa: 'SSA',
	srt: 'SRT',
	subrip: 'SRT',
	webvtt: 'WebVTT',
	pgs: 'PGS'
};

function codecLabel(name: string | null | undefined): string | null {
	if (!name) return null;
	return CODEC_LABELS[name.toLowerCase()] ?? name.toUpperCase();
}

function capitalize(value: string): string {
	return value.charAt(0).toUpperCase() + value.slice(1);
}

function trackName(title: string | null | undefined, language: string | null | undefined): string {
	if (title?.trim()) return title.trim();
	const label = languageLabel(language);
	return label ? `${label} subs` : 'Subtitles';
}

export function formatVideo(stream: ProbeStream | null | undefined): string | null {
	if (!stream?.height && !stream?.codec_name) return null;
	const parts: string[] = [];
	if (stream.height) parts.push(`${stream.height}p`);
	const codec = codecLabel(stream.codec_name);
	if (codec) parts.push(codec);
	parts.push(stream.hdr_format ?? (stream.is_hdr ? 'HDR' : 'SDR'));
	return parts.join(' ');
}

export function formatAudio(stream: ProbeStream): string {
	const parts: string[] = [];
	const name = stream.title?.trim() || languageLabel(stream.language);
	if (name) parts.push(name);
	const codec = codecLabel(stream.codec_name);
	if (codec) parts.push(codec);
	const layout = stream.channel_layout?.replace(/\(.*?\)/g, '').trim();
	const channels = layout
		? capitalize(layout)
		: stream.channels === 2
			? 'Stereo'
			: stream.channels === 1
				? 'Mono'
				: stream.channels
					? `${stream.channels} channels`
					: null;
	if (channels) parts.push(channels);
	if (stream.is_default) parts.push('Default');
	return parts.join(' - ');
}

export function formatSubtitle(stream: ProbeStream): string {
	const parts: string[] = [trackName(stream.title, stream.language)];
	if (stream.is_default) parts.push('Default');
	if (stream.is_forced) parts.push('Forced');
	const codec = codecLabel(stream.codec_name);
	if (codec) parts.push(codec);
	return parts.join(' - ');
}

function formatExternalSubtitle(subtitle: Subtitle): string {
	const parts: string[] = [trackName(subtitle.name, subtitle.language)];
	const format = codecLabel(subtitle.format);
	if (format) parts.push(format);
	return parts.join(' - ');
}

/** Collect display labels from a file's probed streams and sidecar subtitles. */
export function buildMediaInfo(file: MediaFile | null | undefined): MediaInfo {
	if (!file) return { video: null, audio: [], subtitles: [] };
	const streams = file.streams ?? [];
	return {
		video: formatVideo(streams.find((stream) => stream.codec_type === 'video')),
		audio: streams
			.filter((stream) => stream.codec_type === 'audio')
			.map((stream) => ({ value: `stream:${stream.index}`, label: formatAudio(stream) })),
		subtitles: [
			...streams
				.filter((stream) => stream.codec_type === 'subtitle')
				.map((stream) => ({ value: `stream:${stream.index}`, label: formatSubtitle(stream) })),
			...(file.subtitles ?? []).map((subtitle) => ({
				value: `external:${subtitle.id}`,
				label: formatExternalSubtitle(subtitle)
			}))
		]
	};
}
