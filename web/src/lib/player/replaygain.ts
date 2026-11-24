/**
 * Client-side ReplayGain for the music player.
 *
 * Hearth direct-plays the original audio bytes, so the browser reproduces
 * whatever loudness the master was encoded with. ReplayGain tags already travel
 * from the scanner to the web client on every TrackSummary, but nothing used
 * them. This module turns those tags into a linear gain and applies it to the
 * player's single <audio> element through a Web Audio GainNode.
 *
 * A GainNode (rather than `HTMLAudioElement.volume`) is needed because the
 * element's volume is clamped to 0..1: quiet masters with a positive ReplayGain
 * need a boost above 1. The element's own volume remains the user's control —
 * per the Web Audio spec it is still applied to audio routed through a
 * MediaElementAudioSourceNode — so user volume and normalization multiply.
 *
 * Routing through Web Audio disables the browser's normal background-media
 * output: mobile browsers suspend AudioContext when the page is hidden, so the
 * music stops when another app is opened. Leveling is therefore OPT-IN. The
 * default path leaves the `<audio>` element on direct output, which is what
 * keeps playback alive in the background.
 */

/** localStorage key for the opt-in ReplayGain preference. */
export const REPLAY_GAIN_STORAGE_KEY = 'hearth.replay_gain';

/** Read the stored preference. Absent, blocked, or unknown values mean off. */
export function readReplayGainPreference(
	storage: Pick<Storage, 'getItem'> | null | undefined
): boolean {
	if (!storage) return false;
	try {
		return storage.getItem(REPLAY_GAIN_STORAGE_KEY) === '1';
	} catch {
		return false;
	}
}

/** Persist the preference; a blocked storage keeps the in-memory choice only. */
export function writeReplayGainPreference(
	storage: Pick<Storage, 'setItem'> | null | undefined,
	enabled: boolean
): void {
	if (!storage) return;
	try {
		storage.setItem(REPLAY_GAIN_STORAGE_KEY, enabled ? '1' : '0');
	} catch {
		/* private mode or disabled storage: preference lasts for this session */
	}
}

/** Upper bound on the linear gain (~+12 dB), to avoid absurd boosts. */
const MAX_LINEAR_GAIN = 4;

/** ReplayGain tags as carried on a player track. */
export interface ReplayGainFields {
	replayGainTrackGain?: number | null;
	replayGainTrackPeak?: number | null;
	replayGainAlbumGain?: number | null;
	replayGainAlbumPeak?: number | null;
}

/**
 * Convert ReplayGain metadata into a linear amplitude multiplier.
 *
 * Album gain is preferred so an album keeps its internal dynamics; track gain is
 * the fallback for shuffled/single tracks without album tags. The matching
 * peak is used to limit any boost so the result stays at or below 0 dBFS.
 * Returns 1 (no change) when no usable tag is present.
 */
export function replayGainLinear(track: ReplayGainFields | null | undefined): number {
	if (!track) return 1;

	const albumGain = track.replayGainAlbumGain;
	const useAlbum = albumGain != null && Number.isFinite(albumGain);
	const gainDb = useAlbum ? albumGain : track.replayGainTrackGain;
	if (gainDb == null || !Number.isFinite(gainDb)) return 1;

	const peak = useAlbum ? track.replayGainAlbumPeak : track.replayGainTrackPeak;
	let linear = Math.pow(10, gainDb / 20);
	if (peak != null && Number.isFinite(peak) && peak > 0) {
		// Never let the boost push the sample peak past full scale.
		linear = Math.min(linear, 1 / peak);
	}
	linear = Math.min(linear, MAX_LINEAR_GAIN);
	if (!Number.isFinite(linear)) return 1;
	return Math.max(0, linear);
}

type AudioContextConstructor = new () => AudioContext;

function getAudioContextConstructor(): AudioContextConstructor | null {
	if (typeof window === 'undefined') return null;
	const w = window as typeof window & { webkitAudioContext?: AudioContextConstructor };
	return w.AudioContext ?? w.webkitAudioContext ?? null;
}

/**
 * Owns the Web Audio graph for ReplayGain: source -> gain -> destination.
 * One instance lives with the persistent AudioBar. The graph is recreated when
 * the underlying <audio> element is replaced (playback stopped and restarted).
 */
export class ReplayGainGraph {
	private context: AudioContext | null = null;
	private gain: GainNode | null = null;
	private element: HTMLAudioElement | null = null;
	private linear = 1;

	/** Attach to an audio element. Safe to call repeatedly and on reconnect. */
	connect(element: HTMLAudioElement): void {
		if (this.gain && this.element === element) return;
		this.disconnect();
		const AudioContextCtor = getAudioContextConstructor();
		if (!AudioContextCtor) return;
		try {
			const context = new AudioContextCtor();
			const source = context.createMediaElementSource(element);
			const gain = context.createGain();
			gain.gain.value = this.linear;
			source.connect(gain);
			gain.connect(context.destination);
			this.context = context;
			this.gain = gain;
			this.element = element;
		} catch {
			// No Web Audio (or unsupported element): playback continues unlevelled.
			this.disconnect();
		}
	}

	/** Release the graph when the audio element goes away. */
	disconnect(): void {
		const context = this.context;
		this.context = null;
		this.gain = null;
		this.element = null;
		if (context) void context.close().catch(() => {});
	}

	/** Browsers start AudioContext suspended until a user gesture. */
	resume(): void {
		const context = this.context;
		if (context && context.state === 'suspended') void context.resume().catch(() => {});
	}

	/** Set the multiplier, ramping briefly to avoid clicks on track changes. */
	setLinear(linear: number): void {
		this.linear = linear;
		const gain = this.gain;
		const context = this.context;
		if (!gain || !context) return;
		const now = context.currentTime;
		gain.gain.cancelScheduledValues(now);
		gain.gain.setValueAtTime(gain.gain.value, now);
		gain.gain.linearRampToValueAtTime(linear, now + 0.03);
	}
}
