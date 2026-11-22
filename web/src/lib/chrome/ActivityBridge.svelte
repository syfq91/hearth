<script lang="ts">
	/**
	 * Forwards every "the user is still there" signal to a single chrome
	 * controller. Mounted near the top of a viewer's template so its window
	 * listeners are registered before lazily loaded readers add their own
	 * page-turn handlers (a page turn must hide the chrome, not reveal it).
	 */
	interface Props {
		onactivity: () => void;
	}

	let { onactivity }: Props = $props();
</script>

<svelte:window
	onpointermove={onactivity}
	onpointerdown={onactivity}
	onkeydown={onactivity}
	onfocusin={onactivity}
/>
