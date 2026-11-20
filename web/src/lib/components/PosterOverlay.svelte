<script lang="ts">
	import { api } from '../api/client';
	import { Check, Heart, Play } from '@lucide/svelte';

	interface Props {
		itemId: string;
		title: string;
		/** Destination for the centre play control. */
		playHref: string;
		favorite?: boolean;
		watched?: boolean;
		/** Books store completion in reading progress rather than the generic watched flag. */
		bookProgress?: { locator: string; percentage: number; device_name?: string | null } | null;
		/** Hide the played/read toggle (e.g. for music albums). */
		showWatched?: boolean;
		/** Start media in-page instead of navigating (e.g. queue an album). */
		onPlay?: () => void;
		/** Lets the parent keep its own watched indicator in sync. */
		onWatchedChange?: (watched: boolean) => void;
	}

	let {
		itemId,
		title,
		playHref,
		favorite = false,
		watched = false,
		bookProgress = null,
		showWatched = true,
		onPlay,
		onWatchedChange
	}: Props = $props();

	let favoriteOverride = $state<boolean | null>(null);
	let watchedOverride = $state<boolean | null>(null);
	let favoritePending = $state(false);
	let watchedPending = $state(false);

	const favoriteState = $derived(favoriteOverride ?? favorite);
	const watchedState = $derived(watchedOverride ?? watched);

	async function toggleFavorite(): Promise<void> {
		if (favoritePending) return;
		const previous = favoriteState;
		const next = !previous;
		favoriteOverride = next;
		favoritePending = true;
		try {
			await api.post(`/items/${itemId}/favorite`, { body: { favorite: next } });
		} catch {
			favoriteOverride = previous;
		} finally {
			favoritePending = false;
		}
	}

	async function toggleWatched(): Promise<void> {
		if (watchedPending) return;
		const previous = watchedState;
		const next = !previous;
		watchedOverride = next;
		onWatchedChange?.(next);
		watchedPending = true;
		try {
			if (bookProgress) {
				await api.put(`/books/${itemId}/progress`, {
					body: {
						completed: next,
						percentage: next ? 100 : bookProgress.percentage,
						locator: bookProgress.locator,
						device_name: bookProgress.device_name
					}
				});
			} else {
				await api.post(`/items/${itemId}/watched`, { body: { watched: next } });
			}
		} catch {
			watchedOverride = previous;
			onWatchedChange?.(previous);
		} finally {
			watchedPending = false;
		}
	}
</script>

<div class="overlay">
	{#if onPlay}
		<button
			type="button"
			class="play-button"
			onclick={onPlay}
			aria-label={`Play ${title}`}
			title="Play"
		>
			<Play size={26} fill="currentColor" strokeWidth={1.5} />
		</button>
	{:else}
		<a class="play-button" href={playHref} aria-label={`Play ${title}`} title="Play">
			<Play size={26} fill="currentColor" strokeWidth={1.5} />
		</a>
	{/if}
	<div class="overlay-actions">
		{#if showWatched}
			<button
				type="button"
				class={['round-button', watchedState && 'active']}
				onclick={toggleWatched}
				disabled={watchedPending}
				aria-pressed={watchedState}
				aria-label={watchedState ? `Mark ${title} as unwatched` : `Mark ${title} as played`}
				title={watchedState ? 'Mark as unwatched' : 'Mark played'}
			>
				<Check size={16} strokeWidth={2.8} />
			</button>
		{/if}
		<button
			type="button"
			class={['round-button', 'favorite-button', favoriteState && 'active']}
			onclick={toggleFavorite}
			disabled={favoritePending}
			aria-pressed={favoriteState}
			aria-label={favoriteState ? `Remove ${title} from favorites` : `Add ${title} to favorites`}
			title={favoriteState ? 'Remove from favorites' : 'Favourite'}
		>
			<Heart size={16} fill={favoriteState ? 'currentColor' : 'none'} />
		</button>
	</div>
</div>

<style>
	.overlay {
		position: absolute;
		inset: 0;
		z-index: 3;
		display: flex;
		align-items: center;
		justify-content: center;
		pointer-events: none;
		opacity: 0;
		transition: opacity 0.15s ease;
	}

	/* The poster wrapper is the hover target; the overlay itself never catches
	   pointer events so the poster link underneath stays clickable. */
	:global(.poster-wrap:hover) .overlay,
	:global(.poster-wrap:focus-within) .overlay {
		opacity: 1;
	}

	.overlay::before {
		content: '';
		position: absolute;
		inset: 0;
		background: linear-gradient(to top, rgba(0, 0, 0, 0.5), transparent 45%);
		pointer-events: none;
	}

	:global(.poster-wrap:hover) .play-button,
	:global(.poster-wrap:hover) .round-button,
	:global(.poster-wrap:focus-within) .play-button,
	:global(.poster-wrap:focus-within) .round-button {
		pointer-events: auto;
	}

	.play-button {
		pointer-events: none;
		border: 0;
		cursor: pointer;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 3rem;
		height: 3rem;
		border-radius: 999px;
		background: rgba(0, 0, 0, 0.55);
		color: #ffffff;
		box-shadow: 0 2px 10px rgba(0, 0, 0, 0.5);
		transition:
			background 0.15s ease,
			transform 0.15s ease;
	}

	.play-button:hover {
		background: rgba(0, 0, 0, 0.8);
		transform: scale(1.08);
		text-decoration: none;
	}

	.overlay-actions {
		position: absolute;
		right: 0.4rem;
		bottom: 0.4rem;
		display: inline-flex;
		gap: 0.35rem;
		pointer-events: none;
	}

	.round-button {
		pointer-events: none;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 1.85rem;
		height: 1.85rem;
		padding: 0;
		border-radius: 999px;
		border: 1px solid rgba(255, 255, 255, 0.4);
		background: rgba(0, 0, 0, 0.55);
		color: #ffffff;
		box-shadow: 0 2px 6px rgba(0, 0, 0, 0.4);
		cursor: pointer;
		transition:
			background 0.15s ease,
			transform 0.15s ease,
			color 0.15s ease,
			border-color 0.15s ease;
	}

	.round-button:hover:not(:disabled) {
		background: rgba(0, 0, 0, 0.85);
		transform: scale(1.1);
	}

	.round-button:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.round-button.active {
		background: #16a34a;
		border-color: #16a34a;
	}

	.favorite-button.active {
		background: rgba(0, 0, 0, 0.75);
		border-color: var(--accent);
		color: var(--accent);
	}

	.play-button:focus-visible,
	.round-button:focus-visible {
		outline: 2px solid var(--accent);
		outline-offset: 2px;
	}
</style>
