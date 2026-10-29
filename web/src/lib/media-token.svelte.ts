/**
 * Unscoped media token for URL-based media elements.
 *
 * `<img>` and `<video>` cannot attach an `Authorization` header, so artwork and
 * stream URLs carry a short-lived media token in the query string
 * (`POST /auth/media-tokens`). One unscoped token covers the whole catalog;
 * playback mints its own item-scoped token (see `mintMediaToken`).
 *
 * The token is refreshed lazily: callers ask for a URL, and the store mints or
 * reuses a token that has not yet expired.
 */
import { mintMediaToken } from './api/client';

/** Refresh this many ms before the server-side expiry. */
const SAFETY_MARGIN_MS = 60_000;

let token: string | null = null;
let expiresAtMs = 0;
let inflight: Promise<string> | null = null;

/** Return a valid unscoped media token, minting one if needed (single-flight). */
export async function getMediaToken(): Promise<string> {
	if (token && Date.now() < expiresAtMs - SAFETY_MARGIN_MS) return token;
	if (inflight) return inflight;
	inflight = (async () => {
		try {
			const response = await mintMediaToken();
			token = response.token;
			expiresAtMs = Date.parse(response.expires_at);
			return response.token;
		} finally {
			inflight = null;
		}
	})();
	return inflight;
}

/** Append `token=` (and any extra query params) to an absolute API URL. */
export async function withMediaToken(
	url: string,
	extra?: Record<string, string | number | undefined>
): Promise<string> {
	const target = new URL(url, window.location.origin);
	for (const [key, value] of Object.entries(extra ?? {})) {
		if (value !== undefined) target.searchParams.set(key, String(value));
	}
	target.searchParams.set('token', await getMediaToken());
	return target.pathname + target.search;
}

/** Drop the cached token (called on logout: tokens are user-scoped). */
export function clearMediaToken(): void {
	token = null;
	expiresAtMs = 0;
}
