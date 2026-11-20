import assert from 'node:assert/strict';
import test from 'node:test';
import type { S } from './api/types.ts';
import { buildMediaInfo, formatAudio, formatSubtitle, formatVideo } from './media-info.ts';

type ProbeStream = S['ProbeStream'];

function stream(values: Partial<ProbeStream> & Pick<ProbeStream, 'codec_type'>): ProbeStream {
	return {
		index: 0,
		is_default: false,
		is_forced: false,
		is_hdr: false,
		...values
	};
}

test('formatVideo matches the detail page sample', () => {
	assert.equal(
		formatVideo(
			stream({ codec_type: 'video', codec_name: 'hevc', width: 1920, height: 1080 })
		),
		'1080p HEVC SDR'
	);
});

test('formatVideo prefers the probed HDR format', () => {
	assert.equal(
		formatVideo(stream({ codec_type: 'video', codec_name: 'hevc', height: 2160, is_hdr: true, hdr_format: 'HDR10' })),
		'2160p HEVC HDR10'
	);
	assert.equal(formatVideo(stream({ codec_type: 'audio' })), null);
});

test('formatAudio matches the detail page sample', () => {
	assert.equal(
		formatAudio(
			stream({
				codec_type: 'audio',
				codec_name: 'aac',
				language: 'jpn',
				channels: 2,
				channel_layout: 'stereo',
				is_default: true
			})
		),
		'Japanese - AAC - Stereo - Default'
	);
});

test('formatSubtitle matches the detail page sample', () => {
	assert.equal(
		formatSubtitle(
			stream({
				codec_type: 'subtitle',
				codec_name: 'ass',
				language: 'eng',
				is_default: true
			})
		),
		'English subs - Default - ASS'
	);
});

test('buildMediaInfo collects every track and sidecar subtitle', () => {
	const info = buildMediaInfo({
		id: 'file-1',
		size_bytes: 1000,
		missing: false,
		subtitles: [{ id: 'sub-1', name: 'Spanish', format: 'srt', language: 'es', url: '/sub' }],
		streams: [
			stream({ codec_type: 'video', codec_name: 'hevc', height: 1080 }),
			stream({ index: 1, codec_type: 'audio', codec_name: 'aac', language: 'jpn', channels: 2, is_default: true }),
			stream({ index: 2, codec_type: 'audio', codec_name: 'aac', language: 'eng', channels: 6, channel_layout: '5.1(side)' }),
			stream({ index: 3, codec_type: 'subtitle', codec_name: 'ass', language: 'eng', is_default: true })
		]
	});
	assert.equal(info.video, '1080p HEVC SDR');
	assert.deepEqual(info.audio.map((track) => track.label), [
		'Japanese - AAC - Stereo - Default',
		'English - AAC - 5.1'
	]);
	assert.deepEqual(info.subtitles.map((track) => track.label), [
		'English subs - Default - ASS',
		'Spanish - SRT'
	]);
	assert.deepEqual(buildMediaInfo(null), { video: null, audio: [], subtitles: [] });
});
