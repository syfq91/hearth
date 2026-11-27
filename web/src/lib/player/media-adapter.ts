/**
 * Unified Media Transport Adapter contract for Direct Play and HLS playback.
 */

import type { HlsQualityLevel, HlsStreamStats } from './hls-engine.ts';

export interface MediaAdapterCallbacks {
	onReady?: () => void;
	onDurationChange?: (rawDuration: number) => void;
	onTimeUpdate?: (rawCurrentTime: number) => void;
	onBuffering?: (waiting: boolean) => void;
	onPlayStateChange?: (paused: boolean) => void;
	onSeeked?: () => void;
	onEnded?: () => void;
	onProgress?: () => void;
	onStats?: (stats: HlsStreamStats) => void;
	onLevelLoaded?: (levels: HlsQualityLevel[]) => void;
	onError?: (message: string) => void;
}

export interface MediaAdapter {
	attach(video: HTMLVideoElement): void;
	seek(rawTargetSeconds: number): Promise<void>;
	play(): Promise<void>;
	pause(): void;
	setPlaybackRate(rate: number): void;
	setQualityLevel?(levelIndex: number): void;
	destroy(): void;
}
