<script lang="ts">
	/**
	 * Comic reader (CBZ/CBR/PDF page mode).
	 *
	 * Consumes Hearth's on-demand page API (`/books/{id}/pages/{n}`) rather
	 * than shipping the whole archive to the browser, and keeps a 5-page
	 * window in memory with priority-ordered prefetch
	 * (docs/development/reader-integration.md §4).
	 */
	import { withMediaToken } from '../media-token.svelte';
	import { untrack } from 'svelte';
	import { ChevronLeft, ChevronRight } from '@lucide/svelte';
	import {
		horizontalSwipe,
		isTap,
		tapZoneAt,
		type Point,
		type TapZone
	} from '../reader/tap-zones';
	import { DEFAULT_COMIC_SETTINGS, type ComicSettings } from '../reader/comic-settings';
	import { singleSpread, spreadAt, spreadCount, spreadIndex, spreadStartFor } from '../reader/spreads';

	interface Props {
		bookId: string;
		pageCount: number;
		/** 1-indexed page to start on (from the stored locator `page:N`). */
		startPage?: number;
		onPageChange?: (page: number) => void;
		/** Middle-third tap toggles the host page's header. */
		onToggleHeader?: () => void;
		/** Fires when a user turn actually changes the displayed page. */
		onPageTurned?: () => void;
		/** Reading direction and single/double page mode. */
		settings?: ComicSettings;
	}

	let {
		bookId,
		pageCount,
		startPage = 1,
		onPageChange,
		onToggleHeader,
		onPageTurned,
		settings = DEFAULT_COMIC_SETTINGS
	}: Props = $props();

	// Seeded once from the server locator; clamped into the valid range.
	let page = $state(untrack(() => Math.min(Math.max(startPage, 1), Math.max(pageCount, 1))));
	/** Resolved URLs for the pages currently in the window. */
	let cache = $state<Record<number, string>>({});
	let loading = $state(false);
	let error = $state<string | null>(null);
	let pageArea = $state<HTMLElement | null>(null);

	const WINDOW_BEFORE = 2;
	const WINDOW_AFTER = 2;

	const direction = $derived(settings.direction);
	const pageMode = $derived(settings.pageMode);
	const currentStart = $derived(
		pageMode === 'double' ? spreadStartFor(page, pageCount) : page
	);
	const view = $derived(
		pageMode === 'double' ? spreadAt(currentStart, pageCount) : singleSpread(page, pageCount)
	);
	const firstPage = $derived(view.pages[0]);
	const percentage = $derived(
		pageCount > 0 ? Math.round((firstPage / pageCount) * 100) : 0
	);
	const counter = $derived(
		pageMode === 'double'
			? `${spreadIndex(currentStart, pageCount)} / ${spreadCount(pageCount)}`
			: `${page} / ${pageCount}`
	);

	async function urlFor(target: number): Promise<string> {
		return withMediaToken(`/api/v1/books/${bookId}/pages/${target}`, { width: 1600 });
	}

	$effect(() => {
		// Only depend on the current view, `bookId`, and `pageCount`
		const current = firstPage;
		const total = pageCount;
		let cancelled = false;

		void (async () => {
			error = null;
			const initialCache = untrack(() => cache);
			if (!initialCache[current]) {
				loading = true;
				try {
					const resolved = await urlFor(current);
					if (cancelled) return;
					cache = { ...untrack(() => cache), [current]: resolved };
				} catch (e) {
					if (cancelled) return;
					error = e instanceof Error ? e.message : String(e);
				} finally {
					if (!cancelled) loading = false;
				}
			}

			// Prefetch the window around `current`, nearest first.
			const offsets = [1, -1, 2, -2];
			for (const offset of offsets) {
				const target = current + offset;
				if (target < 1 || target > total) continue;
				if (untrack(() => cache)[target]) continue;
				try {
					const resolved = await urlFor(target);
					if (cancelled) return;
					if (untrack(() => cache)[target]) continue;
					cache = { ...untrack(() => cache), [target]: resolved };
				} catch {
					// Prefetch is best-effort
				}
			}

			// Trim pages far outside the window so memory stays bounded.
			const keep = new Set<number>();
			for (let i = -WINDOW_BEFORE; i <= WINDOW_AFTER; i += 1) {
				const target = current + i;
				if (target >= 1 && target <= total) keep.add(target);
			}
			const latestCache = untrack(() => cache);
			let needsTrim = false;
			const trimmed: Record<number, string> = {};
			for (const [key, value] of Object.entries(latestCache)) {
				const numKey = Number(key);
				if (keep.has(numKey)) {
					trimmed[numKey] = value;
				} else {
					needsTrim = true;
				}
			}
			if (needsTrim && !cancelled) {
				cache = trimmed;
			}
		})();

		return () => {
			cancelled = true;
		};
	});

	$effect(() => {
		onPageChange?.(firstPage);
	});

	/** Move by whole views (one page, or one two-page spread). */
	function go(delta: number): boolean {
		const target = delta > 0 ? view.next : view.prev;
		const next = Math.min(Math.max(target, 1), Math.max(pageCount, 1));
		const changed = next !== currentStart;
		if (changed) page = next;
		return changed;
	}

	/** Turn in reading direction; RTL reverses the physical page movement. */
	function turn(delta: number): void {
		if (go(direction === 'rtl' ? -delta : delta)) onPageTurned?.();
	}

	// A tap is a pointer press/release with almost no movement; the third of
	// the page that received it decides which way to turn. Movement above the
	// tap threshold is a swipe (handled separately) and never double-turns.
	let pointerStart: (Point & { zone: TapZone }) | null = null;

	function handlePointerDown(event: PointerEvent): void {
		if (event.button !== 0 || !pageArea) return;
		if ((event.target as HTMLElement | null)?.closest('button')) return;
		pointerStart = {
			x: event.clientX,
			y: event.clientY,
			zone: tapZoneAt(event.clientX, pageArea.getBoundingClientRect())
		};
	}

	function handlePointerUp(event: PointerEvent): void {
		const start = pointerStart;
		pointerStart = null;
		if (!start) return;
		const swipe = horizontalSwipe(start, event);
		if (swipe === 'left') {
			turn(1);
			return;
		}
		if (swipe === 'right') {
			turn(-1);
			return;
		}
		if (!isTap(start, event)) return;
		if (start.zone === 'prev') turn(-1);
		else if (start.zone === 'next') turn(1);
		else onToggleHeader?.();
	}

	function handleKeydown(event: KeyboardEvent): void {
		// Arrow keys turn pages anywhere in the reader, including while a
		// header popover has focus. Form fields and popover controls keep
		// their own arrow-key meaning.
		const target = event.target as HTMLElement | null;
		if (
			target &&
			(target.tagName === 'INPUT' ||
				target.tagName === 'SELECT' ||
				target.tagName === 'TEXTAREA' ||
				target.isContentEditable ||
				target.closest('[role="dialog"], [role="menu"]'))
		) {
			return;
		}
		switch (event.key) {
			case 'ArrowRight':
				event.preventDefault();
				event.stopPropagation();
				turn(1);
				break;
			case 'ArrowLeft':
				event.preventDefault();
				event.stopPropagation();
				turn(-1);
				break;
			default:
				break;
		}
	}

</script>

<svelte:window onkeydown={handleKeydown} />

<div class="comic" role="document" aria-label={`Page ${page} of ${pageCount}`}>
	<div
		class="page-area"
		role="presentation"
		bind:this={pageArea}
		onpointerdown={handlePointerDown}
		onpointerup={handlePointerUp}
		onpointercancel={() => (pointerStart = null)}
	>
		{#if error}
			<p class="banner" role="alert">Could not load page: {error}</p>
		{:else if cache[firstPage]}
			<div class="pages" class:double={pageMode === 'double'} class:rtl={direction === 'rtl'}>
				{#each view.pages as number (number)}
					<img
						class="page"
						src={cache[number]}
						alt={`Page ${number}`}
						decoding="async"
						draggable={false}
					/>
				{/each}
			</div>
		{:else}
			<p class="status" role="status">Loading page…</p>
		{/if}

		<button
			type="button"
			class="edge-nav prev"
			onclick={() => turn(-1)}
			disabled={view.prev === currentStart}
			aria-label={direction === 'rtl' ? 'Next page' : 'Previous page'}
			title={direction === 'rtl' ? 'Next page' : 'Previous page'}
		>
			<ChevronLeft size={28} />
		</button>
		<button
			type="button"
			class="edge-nav next"
			onclick={() => turn(1)}
			disabled={view.next === currentStart}
			aria-label={direction === 'rtl' ? 'Previous page' : 'Next page'}
			title={direction === 'rtl' ? 'Previous page' : 'Next page'}
		>
			<ChevronRight size={28} />
		</button>
	</div>

	<div class="controls">
		<span class="percentage" aria-live="polite">{percentage}%</span>
		<span class="counter" aria-live="polite">{counter}</span>
	</div>

	{#if loading}
		<p class="visually-hidden" role="status">Fetching page</p>
	{/if}
</div>

<style>
	.comic {
		display: flex;
		flex-direction: column;
		align-items: center;
		height: 100%;
		flex: 1;
		min-height: 0;
		gap: 0.5rem;
		padding: 0.5rem 0;
	}

	/* The page fills only the space actually left over, so the audio dock
	   cannot push the controls out of view when music is playing. */
	.page-area {
		position: relative;
		flex: 1;
		min-height: 0;
		width: 100%;
		touch-action: pan-y;
		user-select: none;
	}

	.edge-nav {
		position: absolute;
		top: 50%;
		transform: translateY(-50%);
		z-index: 2;
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2.75rem;
		height: 2.75rem;
		padding: 0;
		background: rgba(0, 0, 0, 0.45);
		border: 1px solid var(--border);
		border-radius: 999px;
		color: #fff;
		cursor: pointer;
		opacity: 0.75;
		transition:
			opacity 0.15s ease,
			background 0.15s ease;
	}

	.edge-nav:hover:not(:disabled) {
		opacity: 1;
		background: rgba(0, 0, 0, 0.7);
	}

	.edge-nav:disabled {
		opacity: 0.25;
		cursor: default;
	}

	.edge-nav.prev {
		left: 0.75rem;
	}

	.edge-nav.next {
		right: 0.75rem;
	}

	.pages {
		display: flex;
		gap: 0;
		width: 100%;
		height: 100%;
		min-height: 0;
	}

	/* Manga spreads read right-to-left, so the higher page sits on the left. */
	.pages.rtl {
		flex-direction: row-reverse;
	}

	.page {
		display: block;
		flex: 1 1 0;
		width: 0;
		min-width: 0;
		height: 100%;
		object-fit: contain;
		background: #000;
		border-radius: var(--radius);
	}

	.controls {
		display: flex;
		align-items: center;
		gap: 1rem;
		align-self: stretch;
		flex-shrink: 0;
		padding: 0 1rem;
	}

	.percentage,
	.counter {
		flex: 1;
		color: var(--muted);
		font-variant-numeric: tabular-nums;
	}

	.percentage {
		text-align: left;
	}

	.counter {
		text-align: right;
	}

	.status {
		color: var(--muted);
		padding: 2rem;
	}

	.banner {
		background: rgba(220, 38, 38, 0.15);
		border: 1px solid rgba(220, 38, 38, 0.4);
		color: #fca5a5;
		padding: 1rem 1.5rem;
		border-radius: var(--radius);
	}
</style>
