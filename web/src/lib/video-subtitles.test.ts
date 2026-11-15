import test from 'node:test';
import assert from 'node:assert';
import { attachmentFontUrls, toAss, toAssTimestamp } from './video-subtitles.ts';

test('attachmentFontUrls keeps the server token for direct play and HLS', () => {
	const urls = attachmentFontUrls([
		{ url: '/api/v1/media/file-1/attachments/2?token=media-token' },
		{ url: '/api/v1/media/file-1/attachments/3?token=media-token' }
	]);
	assert.deepEqual(urls, [
		'/api/v1/media/file-1/attachments/2?token=media-token',
		'/api/v1/media/file-1/attachments/3?token=media-token'
	]);
	assert.deepEqual(attachmentFontUrls(undefined), []);
});

test('toAssTimestamp converts mm:ss.ms and hh:mm:ss.ms', () => {
	assert.equal(toAssTimestamp('01:23.456'), '0:01:23.45');
	assert.equal(toAssTimestamp('01:23,456'), '0:01:23.45');
	assert.equal(toAssTimestamp('02:15:30.500'), '2:15:30.50');
	assert.equal(toAssTimestamp('00:00:03.00'), '0:00:03.00');
});

test('toAss preserves ASS / SSA content regardless of section casing', () => {
	const assStandard = `[Script Info]\nScriptType: v4.00+\n[Events]\nDialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello`;
	assert.equal(toAss(assStandard), assStandard);

	const assLower = `[events]\nFormat: Start, End, Text\nDialogue: 0:00:03.00,0:00:04.00,Hello from ASS`;
	assert.equal(toAss(assLower), assLower);

	const assStyles = `[V4+ Styles]\nStyle: Default\n[Events]\nDialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello`;
	assert.equal(toAss(assStyles), assStyles);
});

test('toAss converts WebVTT cues into valid ASS dialogues', () => {
	const vtt = `WEBVTT\n\n00:01.000 --> 00:03.000\nHello World\n\n00:04.000 --> 00:06.000\nSecond line\nof text\n`;
	const converted = toAss(vtt);

	assert.ok(converted.includes('[Script Info]'));
	assert.ok(converted.includes('[Events]'));
	assert.ok(converted.includes('Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Hello World'));
	assert.ok(converted.includes('Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,Second line\\Nof text'));
});
