/** Shared UI state for the two music player views (mini dock + full page). */

import { readReplayGainPreference, writeReplayGainPreference } from './replaygain';

export const playerUi = $state<{
	/** Full-page player overlay. */
	expanded: boolean;
	/** Tokenized artwork URL resolved by the component that owns the audio element. */
	artworkSrc: string | null;
	currentTime: number;
	duration: number;
	volume: number;
	/** Mirrors the reader header's visibility so the dock can fade with it. */
	readerChromeVisible: boolean;
	/**
	 * Opt-in ReplayGain leveling. Off by default: the Web Audio graph it needs
	 * suspends in background tabs on mobile, stopping the music.
	 */
	replayGain: boolean;
}>({
	expanded: false,
	artworkSrc: null,
	currentTime: 0,
	duration: 0,
	volume: 1,
	readerChromeVisible: true,
	replayGain: false
});

function browserStorage(): Storage | null {
	return typeof localStorage === 'undefined' ? null : localStorage;
}

/** Load the persisted preference before the audio element is created. */
export function loadReplayGain(): void {
	playerUi.replayGain = readReplayGainPreference(browserStorage());
}

/** Toggle leveling and remember the choice for future visits. */
export function setReplayGain(enabled: boolean): void {
	playerUi.replayGain = enabled;
	writeReplayGainPreference(browserStorage(), enabled);
}

/** Last non-zero volume, for mute toggling. */
let lastVolume = 1;

export function openFullPlayer(): void {
	playerUi.expanded = true;
}

export function closeFullPlayer(): void {
	playerUi.expanded = false;
}

export function toggleMute(): void {
	if (playerUi.volume > 0) {
		lastVolume = playerUi.volume;
		playerUi.volume = 0;
	} else {
		playerUi.volume = lastVolume > 0 ? lastVolume : 1;
	}
}

/** Keep `lastVolume` in sync after a direct slider change. */
export function rememberVolume(value: number): void {
	if (value > 0) lastVolume = value;
}

export function formatTime(seconds: number): string {
	if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
	const m = Math.floor(seconds / 60);
	const s = Math.floor(seconds % 60);
	return `${m}:${s.toString().padStart(2, '0')}`;
}
