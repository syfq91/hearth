<script lang="ts">
	import { withMediaToken } from '../media-token.svelte';
	import { blurhash } from '../blurhash';
	import type { S } from '../api/types';

	interface Props {
		image: S['ImageResponse'] | null | undefined;
		/** Width in CSS pixels; drives the API variant width. */
		width?: number;
		alt: string;
		/** CSS aspect ratio for the placeholder box (e.g. `2 / 3`). */
		aspect?: string;
	}

	let { image, width = 300, alt, aspect = '2 / 3' }: Props = $props();

	let src = $state<string | null>(null);
	let loaded = $state(false);

	const dimensions = $derived.by(() => {
		let ratio = 2 / 3;
		if (image?.width && image?.height && image.width > 0 && image.height > 0) {
			ratio = image.width / image.height;
		} else if (aspect) {
			const parts = aspect.split('/').map((s) => parseFloat(s.trim()));
			if (parts.length === 2 && parts[0] > 0 && parts[1] > 0) {
				ratio = parts[0] / parts[1];
			}
		}

		if (ratio >= 1) {
			return { width: 32, height: Math.max(16, Math.round(32 / ratio)) };
		}
		return { width: Math.max(16, Math.round(48 * ratio)), height: 48 };
	});

	$effect(() => {
		loaded = false;
		if (!image) {
			src = null;
			return;
		}
		let cancelled = false;
		void withMediaToken(image.url, { width }).then((resolved) => {
			if (!cancelled) src = resolved;
		});
		return () => {
			cancelled = true;
		};
	});

	function initImg(el: HTMLImageElement) {
		if (el.complete && el.naturalWidth > 0) {
			loaded = true;
		}
	}
</script>

<div class="poster" style:aspect-ratio={aspect}>
	{#if image?.blurhash}
		<canvas
			use:blurhash={{
				hash: image.blurhash,
				width: dimensions.width,
				height: dimensions.height
			}}
			class="placeholder-canvas"
			aria-hidden="true"
		></canvas>
	{:else}
		<div class="placeholder" aria-hidden="true"></div>
	{/if}

	{#if src}
		<img
			{src}
			{alt}
			loading="lazy"
			decoding="async"
			class:loaded
			onload={() => (loaded = true)}
			use:initImg
		/>
	{/if}
</div>

<style>
	.poster {
		position: relative;
		width: 100%;
		border-radius: var(--radius);
		overflow: hidden;
		background: var(--surface-2);
	}

	.placeholder-canvas,
	.placeholder {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.placeholder-canvas {
		filter: blur(12px);
		transform: scale(1.15);
	}

	.placeholder {
		background: linear-gradient(135deg, var(--surface-2), var(--surface));
	}

	.poster img {
		position: absolute;
		inset: 0;
		width: 100%;
		height: 100%;
		object-fit: cover;
		opacity: 0;
		transition: opacity 0.25s ease-in-out;
	}

	.poster img.loaded {
		opacity: 1;
	}
</style>
