/**
 * Session state for the SPA (Svelte 5 runes).
 *
 * Tokens are persisted in localStorage (see ADR for client token storage);
 * the access token is refreshed transparently by `./api/client` on 401.
 */
import {
	api,
	applyTokenPair,
	clearTokens,
	ensureFreshTokens,
	getAccessToken,
	getRefreshToken,
	hydrateTokens
} from './api/client';
import { clearMediaToken } from './media-token.svelte';
import type { TokenPair, UserResponse } from './api/types';

export type AuthStatus = 'loading' | 'anonymous' | 'authenticated';

export interface SessionState {
	status: AuthStatus;
	user: UserResponse | null;
}

export const session = $state<SessionState>({
	status: 'loading',
	user: null
});

let bootstrapPromise: Promise<void> | null = null;

/**
 * Resolve the initial session exactly once per page load: hydrate stored
 * tokens, then read `/auth/me` (the client refreshes on 401 when possible).
 */
export function bootstrap(): Promise<void> {
	bootstrapPromise ??= (async () => {
		hydrateTokens();
		if (getAccessToken() || getRefreshToken()) {
			if (!getAccessToken()) await ensureFreshTokens();
			try {
				session.user = await api.get<UserResponse>('/auth/me');
				session.status = 'authenticated';
				return;
			} catch {
				/* fall through to anonymous */
			}
		}
		clearTokens();
		session.user = null;
		session.status = 'anonymous';
	})();
	return bootstrapPromise;
}

/** Password login. Throws {@link ApiError} on failure. */
export async function login(username: string, password: string): Promise<void> {
	const pair = await api.post<TokenPair>('/auth/login', {
		body: { username, password },
		auth: false
	});
	applyTokenPair(pair);
	session.user = await api.get<UserResponse>('/auth/me');
	session.status = 'authenticated';
}

/** First-run admin setup. Only allowed while zero users exist (409 after). */
export async function setup(username: string, password: string): Promise<void> {
	const pair = await api.post<TokenPair>('/auth/setup', {
		body: { username, password },
		auth: false
	});
	applyTokenPair(pair);
	session.user = await api.get<UserResponse>('/auth/me');
	session.status = 'authenticated';
}

/** Revoke the refresh-token family server-side, then drop local state. */
export async function logout(): Promise<void> {
	const refresh_token = getRefreshToken();
	try {
		if (refresh_token) {
			await api.post<void>('/auth/logout', { body: { refresh_token } });
		}
	} catch {
		/* token already dead; local logout must still succeed */
	}
	clearTokens();
	clearMediaToken();
	session.user = null;
	session.status = 'anonymous';
}

/** Re-read the account after a profile edit. */
export async function reloadUser(): Promise<void> {
	session.user = await api.get<UserResponse>('/auth/me');
}
