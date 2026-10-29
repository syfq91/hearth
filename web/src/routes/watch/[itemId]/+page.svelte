<script lang="ts">
	import VideoPlayer from '../../../lib/components/VideoPlayer.svelte';
	import { findNextEpisode, type NextEpisode } from '../../../lib/api/next-episode';
	import { goto } from '$app/navigation';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let ended = $state(false);
	let next = $state<NextEpisode | null>(null);
	let dismissed = $state(false);
	let dialogEl = $state<HTMLDivElement | null>(null);
	/** Focus before the dialog opened, restored on dismiss. */
	let returnFocus: HTMLElement | null = null;

	// A new episode (or a reload) clears any previous up-next prompt.
	$effect(() => {
		data.item.id;
		ended = false;
		next = null;
		dismissed = false;
	});

	// Move focus into the prompt when it appears, so keyboard and D-pad users
	// are not left on the finished video.
	$effect(() => {
		if (ended && next && !dismissed) {
			dialogEl?.querySelector<HTMLButtonElement>('button')?.focus({ preventScroll: true });
		}
	});

	async function handleEnded(): Promise<void> {
		ended = true;
		returnFocus = document.activeElement as HTMLElement | null;
		if (data.item.type !== 'episode') return;
		try {
			next = await findNextEpisode(data.item.id);
		} catch {
			next = null;
		}
	}

	function dismiss(): void {
		dismissed = true;
		returnFocus?.focus({ preventScroll: true });
	}

	function playNext(): void {
		if (!next) return;
		void goto(`/watch/${next.id}`);
	}
</script>

<svelte:head>
	<title>{data.item.title} · Hearth</title>
</svelte:head>

<div class="watch">
	<VideoPlayer
		itemId={data.item.id}
		startAtMs={data.startAtMs}
		title={data.item.title}
		onEnded={handleEnded}
	/>

	{#if ended && next && !dismissed}
		<div class="upnext" role="dialog" aria-label="Up next" bind:this={dialogEl}>
			<p class="kicker">Up next</p>
			<h2>{next.title}</h2>
			{#if next.label}
				<p class="label">{next.label}</p>
			{/if}
			<div class="actions">
				<button type="button" class="play" onclick={playNext}>Play next episode</button>
				<button type="button" class="ghost" onclick={dismiss}>Dismiss</button>
			</div>
		</div>
	{/if}
</div>

<style>
	.watch {
		position: relative;
	}

	.upnext {
		position: absolute;
		right: 1rem;
		bottom: 4.5rem;
		width: min(22rem, calc(100% - 2rem));
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 1rem 1.25rem;
		box-shadow: 0 12px 32px rgb(0 0 0 / 45%);
		z-index: 30;
	}

	.kicker {
		margin: 0 0 0.25rem;
		font-size: 0.75rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--muted);
	}

	.upnext h2 {
		margin: 0;
		font-size: 1.1rem;
	}

	.label {
		margin: 0.25rem 0 0;
		color: var(--muted);
		font-size: 0.9rem;
	}

	.actions {
		display: flex;
		gap: 0.6rem;
		margin-top: 0.9rem;
	}

	.play {
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		border: 0;
		border-radius: var(--radius);
		padding: 0.5rem 1.1rem;
		cursor: pointer;
	}

	.ghost {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.5rem 1.1rem;
		cursor: pointer;
		color: inherit;
	}
</style>
