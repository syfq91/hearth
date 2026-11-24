<script lang="ts">
	import Detail from './Detail.svelte';
	import RouteTitle from '../../../lib/components/RouteTitle.svelte';
	import type { PageProps } from './$types';

	let { data: pageData }: PageProps = $props();
</script>

<RouteTitle value={pageData.detail} label={(d) => d.library.name} />

{#await pageData.detail then detail}
	<Detail data={detail} />
{:catch error}
	<p role="alert">{error instanceof Error ? error.message : String(error)}</p>
{/await}
