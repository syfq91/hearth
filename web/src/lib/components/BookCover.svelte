<script lang="ts">
	import { withMediaToken } from '../media-token.svelte';

	interface Props {
		coverImageId: string | null | undefined;
		alt: string;
		aspect?: string;
		width?: number;
		/** First visible card: load eagerly with high fetch priority. */
		priority?: boolean;
	}

	let { coverImageId, alt, aspect = '2 / 3', width = 300, priority = false }: Props = $props();

	let src = $state<string | null>(null);

	$effect(() => {
		if (!coverImageId) {
			src = null;
			return;
		}
		let cancelled = false;
		void withMediaToken(`/api/v1/images/${coverImageId}`, { width }).then((resolved) => {
			if (!cancelled) src = resolved;
		});
		return () => {
			cancelled = true;
		};
	});
</script>

<div class="cover" style:aspect-ratio={aspect}>
	{#if src}
		<img
			{src}
			{alt}
			loading={priority ? 'eager' : 'lazy'}
			fetchpriority={priority ? 'high' : 'auto'}
			decoding="async"
		/>
	{:else}
		<div class="placeholder" aria-hidden="true"></div>
	{/if}
</div>

<style>
	.cover {
		position: relative;
		width: 100%;
		border-radius: var(--radius);
		overflow: hidden;
		background: var(--surface-2);
	}

	.cover img {
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
