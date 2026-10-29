/**
 * Typed fetch layer for the Hearth API.
 *
 * - Same-origin `/api/v1` base URL (FastAPI serves the SPA at `/`).
 * - Bearer access token with proactive refresh: a 401 caused by an expired or
 *   revoked access token triggers a single-flight refresh and one retry.
 * - The server's unified error envelope (`{"error": {code, message, details}}`)
 *   is surfaced as {@link ApiError}.
 * - Refresh tokens rotate on every use (see docs/API.md §2.3), so the stored
 *   refresh token is replaced on each successful refresh.
 */
import type { MediaTokenResponse, TokenPair } from './types';

const BASE = '/api/v1';
const ACCESS_KEY = 'hearth.access';
const REFRESH_KEY = 'hearth.refresh';

/** Error raised for any non-2xx API response. */
export class ApiError extends Error {
	readonly status: number;
	readonly code: string;
	readonly details: Record<string, unknown>;

	constructor(
		status: number,
		code: string,
		message: string,
		details: Record<string, unknown> = {}
	) {
		super(message);
		this.name = 'ApiError';
		this.status = status;
		this.code = code;
		this.details = details;
	}
}

type QueryValue = string | number | boolean | undefined | null;

export interface RequestOptions {
	query?: Record<string, QueryValue>;
	body?: unknown;
	/** Attach the bearer token and refresh on 401 (default true). */
	auth?: boolean;
	signal?: AbortSignal;
	headers?: Record<string, string>;
}

let accessToken: string | null = null;
let refreshInFlight: Promise<boolean> | null = null;

function readStored(key: string): string | null {
	try {
		return localStorage.getItem(key);
	} catch {
		return null;
	}
}

function writeStored(key: string, value: string | null): void {
	try {
		if (value === null) {
			localStorage.removeItem(key);
		} else {
			localStorage.setItem(key, value);
		}
	} catch {
		/* storage unavailable (private mode): tokens stay in memory */
	}
}

/** Load the persisted access token into memory (call once at app start). */
export function hydrateTokens(): void {
	accessToken = readStored(ACCESS_KEY);
}

export function getAccessToken(): string | null {
	return accessToken;
}

export function getRefreshToken(): string | null {
	return readStored(REFRESH_KEY);
}

export function setAccessToken(token: string | null): void {
	accessToken = token;
	writeStored(ACCESS_KEY, token);
}

export function setRefreshToken(token: string | null): void {
	writeStored(REFRESH_KEY, token);
}

export function clearTokens(): void {
	setAccessToken(null);
	setRefreshToken(null);
}

/** Store a fresh token pair returned by login/setup/refresh. */
export function applyTokenPair(pair: TokenPair): void {
	setAccessToken(pair.access_token);
	setRefreshToken(pair.refresh_token);
}

/**
 * Exchange the stored refresh token for a new pair (single-flight).
 * Returns false when no refresh token exists or the exchange fails, in which
 * case any stored tokens are cleared.
 */
export async function ensureFreshTokens(): Promise<boolean> {
	if (refreshInFlight) return refreshInFlight;
	const refresh_token = getRefreshToken();
	if (!refresh_token) return false;
	refreshInFlight = (async () => {
		try {
			const response = await fetch(`${BASE}/auth/refresh`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ refresh_token })
			});
			if (!response.ok) {
				clearTokens();
				return false;
			}
			const pair = (await response.json()) as TokenPair;
			applyTokenPair(pair);
			return true;
		} catch {
			return false;
		} finally {
			refreshInFlight = null;
		}
	})();
	return refreshInFlight;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
	const url = new URL(BASE + path, window.location.origin);
	for (const [key, value] of Object.entries(query ?? {})) {
		if (value !== undefined && value !== null && value !== '') {
			url.searchParams.set(key, String(value));
		}
	}
	return url.pathname + url.search;
}

async function toApiError(response: Response): Promise<ApiError> {
	let code = 'http_error';
	let message = `Request failed with status ${response.status}`;
	let details: Record<string, unknown> = {};
	try {
		const data = (await response.json()) as {
			error?: { code?: unknown; message?: unknown; details?: unknown };
		};
		const envelope = data?.error;
		if (envelope && typeof envelope === 'object') {
			if (typeof envelope.code === 'string') code = envelope.code;
			if (typeof envelope.message === 'string') message = envelope.message;
			if (envelope.details && typeof envelope.details === 'object') {
				details = envelope.details as Record<string, unknown>;
			}
		}
	} catch {
		/* non-JSON body (proxy error page etc.) */
	}
	return new ApiError(response.status, code, message, details);
}

async function doFetch(method: string, url: string, opts: RequestOptions): Promise<Response> {
	const headers: Record<string, string> = { ...(opts.headers ?? {}) };
	const hasBody = opts.body !== undefined;
	if (hasBody) headers['Content-Type'] = 'application/json';
	if (opts.auth !== false && accessToken) {
		headers['Authorization'] = `Bearer ${accessToken}`;
	}
	return fetch(url, {
		method,
		headers,
		body: hasBody ? JSON.stringify(opts.body) : undefined,
		signal: opts.signal
	});
}

/** Perform a typed API request against `/api/v1`. */
export async function request<T>(
	method: string,
	path: string,
	opts: RequestOptions = {}
): Promise<T> {
	const url = buildUrl(path, opts.query);
	let response = await doFetch(method, url, opts);
	if (response.status === 401 && opts.auth !== false) {
		const error = await toApiError(response);
		if (error.code === 'invalid_token' || error.code === 'token_revoked') {
			if (!(await ensureFreshTokens())) throw error;
			response = await doFetch(method, url, opts);
		} else {
			throw error;
		}
	}
	if (!response.ok) throw await toApiError(response);
	if (response.status === 204) return undefined as T;
	return (await response.json()) as T;
}

/** Verb helpers with the response type supplied by the caller. */
export const api = {
	get: <T>(path: string, opts?: RequestOptions): Promise<T> => request<T>('GET', path, opts),
	post: <T>(path: string, opts?: RequestOptions): Promise<T> => request<T>('POST', path, opts),
	put: <T>(path: string, opts?: RequestOptions): Promise<T> => request<T>('PUT', path, opts),
	patch: <T>(path: string, opts?: RequestOptions): Promise<T> => request<T>('PATCH', path, opts),
	del: <T>(path: string, opts?: RequestOptions): Promise<T> => request<T>('DELETE', path, opts)
};

/** Mint a media token for `<video>`/`<img>`/`<audio>` query strings. */
export async function mintMediaToken(itemId?: string | null): Promise<MediaTokenResponse> {
	return api.post<MediaTokenResponse>('/auth/media-tokens', {
		body: { item_id: itemId ?? null }
	});
}

/** SSE endpoint with a query-string token (docs/API.md §5). */
export function eventsUrl(): string {
	return `${BASE}/events?token=${encodeURIComponent(accessToken ?? '')}`;
}

/** Build an API URL for direct use in media element `src` attributes. */
export function mediaUrl(path: string, query?: Record<string, QueryValue>): string {
	return buildUrl(path, query);
}
