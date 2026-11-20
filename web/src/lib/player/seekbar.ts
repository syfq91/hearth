/**
 * Seekbar time normalization for media with a container presentation offset.
 *
 * HTML media elements (and Hearth's probe data) report timestamps in raw
 * container time. Containers such as MKV can start well after zero, so a raw
 * `currentTime` of 70s may be only 60s into the actual content. The seekbar,
 * its buffered overlay and all time labels work in content time, which is
 * raw time minus the container's `start_time_seconds`.
 *
 * The same conversion must be applied to the denominator (duration) as to the
 * numerator (currentTime / buffered ranges). Mixing the two scales stretches
 * the seekbar and makes the buffered bar drift away from the playhead — most
 * visibly when playback resumes at a saved raw position.
 */

/** Convert a raw container timestamp into the content-time value the UI uses. */
export function contentTime(rawSeconds: number, startOffsetSeconds: number): number {
	return Math.max(0, rawSeconds - Math.max(0, startOffsetSeconds));
}

/**
 * UI timeline length for a media resource. Both the media element's `duration`
 * and the probed file duration report raw container time, so the container
 * offset has to be removed here too. Returns 0 for unknown/non-finite values.
 */
export function contentDuration(rawDuration: number, startOffsetSeconds: number): number {
	if (!Number.isFinite(rawDuration) || rawDuration <= 0) return 0;
	return contentTime(rawDuration, startOffsetSeconds);
}
