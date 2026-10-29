<script lang="ts">
	import { withMediaToken } from '../media-token.svelte';
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

	$effect(() => {
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
</script>

<div class="poster" style:aspect-ratio={aspect}>
	{#if src}
		<img {src} {alt} loading="lazy" decoding="async" />
	{:else}
		<div class="placeholder" aria-hidden="true"></div>
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

	.poster img {
		width: 100%;
		height: 100%;
		object-fit: cover;
	}

	.placeholder {
		width: 100%;
		height: 100%;
		background: linear-gradient(135deg, var(--surface-2), var(--surface));
	}
</style>
