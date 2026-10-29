<script lang="ts">
	import ItemDetail from '../../../lib/components/ItemDetail.svelte';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<ItemDetail item={data.item} />
