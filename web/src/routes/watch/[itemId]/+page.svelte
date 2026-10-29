<script lang="ts">
	import VideoPlayer from '../../../lib/components/VideoPlayer.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<VideoPlayer itemId={data.item.id} startAtMs={data.startAtMs} title={data.item.title} />
