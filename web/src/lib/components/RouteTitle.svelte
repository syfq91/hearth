<!--
	Sets the document title from a load() promise without blocking navigation.
	<svelte:head> must be a component root element, so it cannot live inside
	the {#await} block that renders the page body.
-->
<script lang="ts" generics="T">
	let {
		value,
		label,
		appName = 'Hearth'
	}: {
		value: Promise<T>;
		label: (resolved: T) => string;
		appName?: string;
	} = $props();

	let title = $state('');

	$effect(() => {
		const pending = value;
		title = appName;
		void pending
			.then((resolved) => {
				title = `${label(resolved)} · ${appName}`;
			})
			.catch(() => {});
	});
</script>

<svelte:head>
	<title>{title}</title>
</svelte:head>
