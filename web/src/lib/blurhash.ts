/**
 * BlurHash canvas renderer using fast-blurhash.
 *
 * Renders a compact decoded BlurHash onto an HTMLCanvasElement with an
 * in-memory cache to eliminate redundant decodes during scrolling or re-renders.
 */
import type { ActionReturn } from 'svelte/action';
import { decodeBlurHash } from 'fast-blurhash';

export interface BlurhashParams {
	hash?: string | null;
	width?: number;
	height?: number;
}

const MAX_CACHE_ENTRIES = 500;
const pixelCache = new Map<string, Uint8ClampedArray>();

/** Decode a BlurHash into pixel data with memory caching. */
export function decodeBlurhashPixels(
	hash: string,
	width = 32,
	height = 32
): Uint8ClampedArray | null {
	if (!hash) return null;
	const key = `${hash}:${width}x${height}`;
	const cached = pixelCache.get(key);
	if (cached) return cached;

	try {
		const pixels = decodeBlurHash(hash, width, height);
		if (pixelCache.size >= MAX_CACHE_ENTRIES) {
			const first = pixelCache.keys().next().value;
			if (first) pixelCache.delete(first);
		}
		pixelCache.set(key, pixels);
		return pixels;
	} catch {
		return null;
	}
}

/**
 * Svelte action to draw BlurHash pixels onto an HTMLCanvasElement.
 */
export function blurhash(
	canvas: HTMLCanvasElement,
	params: BlurhashParams
): ActionReturn<BlurhashParams> {
	function draw(p: BlurhashParams) {
		if (!p.hash) return;
		const width = p.width ?? 32;
		const height = p.height ?? 32;

		if (canvas.width !== width) canvas.width = width;
		if (canvas.height !== height) canvas.height = height;

		const ctx = canvas.getContext('2d');
		if (!ctx) return;

		const pixels = decodeBlurhashPixels(p.hash, width, height);
		if (!pixels) return;

		const imageData = ctx.createImageData(width, height);
		imageData.data.set(pixels);
		ctx.putImageData(imageData, 0, 0);
	}

	draw(params);

	return {
		update(newParams: BlurhashParams) {
			if (
				newParams.hash !== params.hash ||
				newParams.width !== params.width ||
				newParams.height !== params.height
			) {
				params = newParams;
				draw(params);
			}
		}
	};
}
