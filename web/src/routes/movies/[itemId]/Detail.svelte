<script lang="ts">
	import ItemDetail from '../../../lib/components/ItemDetail.svelte';
	import type { PageProps } from './$types';

	let { data }: { data: Awaited<PageProps['data']['detail']> } = $props();
</script>

<svelte:head>
	<title>{data.item.title} · Hearth</title>
</svelte:head>

<ItemDetail item={data.item} />
