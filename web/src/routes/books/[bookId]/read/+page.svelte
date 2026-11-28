<script lang="ts">
	// Readers are loaded on demand: opening an EPUB must not download pdf.js or
	// the comic viewer (§7.17.2 of the performance audit).
	import type ComicReaderType from '../../../../lib/components/ComicReader.svelte';
	import type EpubReaderType from '../../../../lib/components/EpubReader.svelte';
	import type PdfReaderType from '../../../../lib/components/PdfReader.svelte';
	import { ProgressSync } from '../../../../lib/reader/progress';
	import { playerUi } from '../../../../lib/player/ui.svelte';
	import { withMediaToken } from '../../../../lib/media-token.svelte';
	import ActivityBridge from '../../../../lib/chrome/ActivityBridge.svelte';
	import { createAutoHideChrome } from '../../../../lib/chrome/auto-hide.ts';
	import '../../../../lib/chrome/chrome.css';
	import { goto } from '$app/navigation';
	import { ArrowLeft, Maximize, Minimize, List, Settings2 } from '@lucide/svelte';
	import {
		FONT_FAMILIES,
		FONT_SIZE_STEP,
		MAX_FONT_SIZE,
		MIN_FONT_SIZE,
		THEMES,
		loadReaderSettings,
		saveReaderSettings,
		type ReaderSettings
	} from '../../../../lib/reader/settings';
	import type { TocItem } from '../../../../lib/reader/toc';
	import {
		loadComicSettings,
		saveComicSettings,
		type ComicSettings
	} from '../../../../lib/reader/comic-settings';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const bookId = $derived(data.book.id);
	const format = $derived(data.format);
	const progress = $derived(data.progress);

	let isNativeFullscreen = $state(false);
	let chromeVisible = $state(true);

	// The audio dock lives in the root layout; on reader pages it follows the
	// header so both fade away together and the page keeps its full height.
	$effect(() => {
		playerUi.readerChromeVisible = chromeVisible;
	});

	// EPUB header controls: table of contents and display settings.
	let epubToc = $state<TocItem[]>([]);
	let tocOpen = $state(false);
	let settingsOpen = $state(false);
	let comicSettingsOpen = $state(false);
	let epubGoToChapter = $state<((href: string) => void) | undefined>(undefined);
	let readerSettings = $state<ReaderSettings>(loadReaderSettings());
	let comicSettings = $state<ComicSettings>(loadComicSettings());

	const chrome = createAutoHideChrome({
		persist: () => tocOpen || settingsOpen || comicSettingsOpen,
		onChange: (visible) => {
			chromeVisible = visible;
		}
	});
	$effect(() => () => chrome.dispose());

	function updateSettings(patch: Partial<ReaderSettings>): void {
		readerSettings = { ...readerSettings, ...patch };
		saveReaderSettings(readerSettings);
	}

	function updateComicSettings(patch: Partial<ComicSettings>): void {
		comicSettings = { ...comicSettings, ...patch };
		saveComicSettings(comicSettings);
	}

	function adjustFontSize(delta: number): void {
		const next = Math.min(
			Math.max(readerSettings.fontSize + delta, MIN_FONT_SIZE),
			MAX_FONT_SIZE
		);
		updateSettings({ fontSize: next });
	}

	function closeHeaderMenus(): void {
		tocOpen = false;
		settingsOpen = false;
		comicSettingsOpen = false;
	}

	function hideChrome(): void {
		chrome.hide();
		closeHeaderMenus();
	}

	function toggleChrome(): void {
		if (chrome.visible) hideChrome();
		else chrome.reveal();
	}

	function selectChapter(item: TocItem): void {
		epubGoToChapter?.(item.href);
		hideChrome();
	}

	// One syncer per mounted reader; cleanly disposed when switching books or unmounting.
	let sync = $state<ProgressSync | null>(null);
	$effect(() => {
		const currentSync = new ProgressSync(bookId);
		sync = currentSync;
		return () => {
			currentSync.dispose();
		};
	});

	/** Comic locator is `page:<n>`; EPUB locator is an EPUB CFI. */
	function parsePage(locator: string | null | undefined): number {
		const match = /^page:(\d+)$/.exec(locator ?? '');
		return match ? Number.parseInt(match[1], 10) : 1;
	}

	const isComic = $derived(format === 'cbz' || format === 'cbr');
	const isPdf = $derived(format === 'pdf');
	const isEpub = $derived(format === 'epub');

	let EpubReader: typeof EpubReaderType | null = $state(null);
	let PdfReader: typeof PdfReaderType | null = $state(null);
	let ComicReader: typeof ComicReaderType | null = $state(null);

	$effect(() => {
		const target = format;
		let cancelled = false;
		EpubReader = null;
		PdfReader = null;
		ComicReader = null;

		if (target === 'epub') {
			void import('../../../../lib/components/EpubReader.svelte').then((m) => {
				if (!cancelled) EpubReader = m.default;
			});
		} else if (target === 'pdf') {
			void import('../../../../lib/components/PdfReader.svelte').then((m) => {
				if (!cancelled) PdfReader = m.default;
			});
		} else if (target === 'cbz' || target === 'cbr') {
			void import('../../../../lib/components/ComicReader.svelte').then((m) => {
				if (!cancelled) ComicReader = m.default;
			});
		}

		return () => {
			cancelled = true;
		};
	});

	// File URL only needed for epub and pdf (ComicReader resolves per-page).
	let fileUrl = $state<string | null>(null);
	$effect(() => {
		if (!format || isComic) return;
		let cancelled = false;
		void withMediaToken(`/api/v1/books/${bookId}/file/${format}`).then((resolved) => {
			if (!cancelled) fileUrl = resolved;
		});
		return () => {
			cancelled = true;
		};
	});

	function handleEpubRelocate(locator: string, percentage: number): void {
		sync?.update({ locator, percentage });
	}

	function handleComicPage(page: number): void {
		const total = data.book.page_count ?? 0;
		const percentage = total > 0 ? Math.round((page / total) * 10000) / 100 : 0;
		sync?.update({ locator: `page:${page}`, percentage });
	}

	function handlePdfPage(page: number, total: number): void {
		const percentage = total > 0 ? Math.round((page / total) * 10000) / 100 : 0;
		sync?.update({ locator: `page:${page}`, percentage });
	}

	async function toggleNativeFullscreen(): Promise<void> {
		try {
			if (!document.fullscreenElement) {
				await document.documentElement.requestFullscreen();
				isNativeFullscreen = true;
			} else {
				await document.exitFullscreen();
				isNativeFullscreen = false;
			}
		} catch {
			/* fullscreen might be restricted by browser policy */
		}
	}

	$effect(() => {
		const onFsChange = () => {
			isNativeFullscreen = Boolean(document.fullscreenElement);
		};
		const onKeydown = (event: KeyboardEvent) => {
			if (event.key !== 'Escape') return;
			if (tocOpen || settingsOpen || comicSettingsOpen) {
				closeHeaderMenus();
			} else if (!document.fullscreenElement) {
				void goto(`/books/${data.book.id}`);
			}
		};
		// ActivityBridge owns reveal/re-arm; this only closes popovers on an
		// outside click, which the shared bridge deliberately knows nothing about.
		const onPointerDown = (event: PointerEvent) => {
			if (!(event.target as HTMLElement | null)?.closest('.popover-anchor')) {
				closeHeaderMenus();
			}
		};
		document.addEventListener('fullscreenchange', onFsChange);
		window.addEventListener('keydown', onKeydown);
		window.addEventListener('pointerdown', onPointerDown);
		return () => {
			document.removeEventListener('fullscreenchange', onFsChange);
			window.removeEventListener('keydown', onKeydown);
			window.removeEventListener('pointerdown', onPointerDown);
		};
	});
</script>

<svelte:head>
	<title>{data.book.title} · Reading · Hearth</title>
</svelte:head>

<ActivityBridge onactivity={chrome.reveal} />

<div class="book-reader-container media-chrome-host" data-chrome-hidden={!chromeVisible}>
	<header class={['reader-header', 'media-chrome', 'media-chrome--top']} data-hidden={!chromeVisible}>
		<div class="reader-header-left">
			<a
				href={`/books/${data.book.id}`}
				class="back-btn"
				onclick={(event) => {
					if (typeof window !== 'undefined' && window.history.length > 1) {
						event.preventDefault();
						window.history.back();
					}
				}}
				aria-label="Back"
				title="Back"
			>
				<ArrowLeft size={22} />
				<span class="visually-hidden">Back</span>
			</a>
			<a href={`/books/${data.book.id}`} class="crumbs" aria-label={data.book.title}>
				{data.book.title}
			</a>
		</div>

		<div class="reader-header-right">
			{#if isEpub}
				<div class="popover-anchor">
					<button
						type="button"
						class="fs-button"
						onclick={() => {
							tocOpen = !tocOpen;
							settingsOpen = false;
						}}
						aria-label="Chapters"
						aria-expanded={tocOpen}
						title="Chapters"
					>
						<List size={18} />
					</button>
					{#if tocOpen}
						<div class="popover toc-popover" role="menu" aria-label="Chapters">
							<div class="popover-heading">Chapters</div>
							<div class="popover-scroll">
								{#each epubToc as item (item.href + (item.id ?? ''))}
									<button
										type="button"
										role="menuitem"
										class="popover-item"
										onclick={() => selectChapter(item)}
									>
										{item.label}
									</button>
								{:else}
									<p class="popover-empty">No chapters</p>
								{/each}
							</div>
						</div>
					{/if}
				</div>

				<div class="popover-anchor">
					<button
						type="button"
						class="fs-button"
						onclick={() => {
							settingsOpen = !settingsOpen;
							tocOpen = false;
						}}
						aria-label="Reader settings"
						aria-expanded={settingsOpen}
						title="Reader settings"
					>
						<Settings2 size={18} />
					</button>
					{#if settingsOpen}
						<div class="popover settings-popover" role="dialog" aria-label="Reader settings">
							<div class="popover-heading">Reader settings</div>

							<div class="setting-row">
								<span class="setting-label">Font size</span>
								<div class="stepper">
									<button
										type="button"
										class="step"
										aria-label="Decrease font size"
										disabled={readerSettings.fontSize <= MIN_FONT_SIZE}
										onclick={() => adjustFontSize(-FONT_SIZE_STEP)}
									>
										−
									</button>
									<span class="stepper-value" aria-live="polite">
										{readerSettings.fontSize}
									</span>
									<button
										type="button"
										class="step"
										aria-label="Increase font size"
										disabled={readerSettings.fontSize >= MAX_FONT_SIZE}
										onclick={() => adjustFontSize(FONT_SIZE_STEP)}
									>
										+
									</button>
								</div>
							</div>

							<div class="setting-row">
								<span class="setting-label">Font style</span>
								<div class="segmented">
									{#each FONT_FAMILIES as family (family)}
										<button
											type="button"
											class={['segment', readerSettings.fontFamily === family && 'active']}
											aria-pressed={readerSettings.fontFamily === family}
											onclick={() => updateSettings({ fontFamily: family })}
										>
											{family === 'serif' ? 'Serif' : family === 'sans' ? 'Sans' : 'Mono'}
										</button>
									{/each}
								</div>
							</div>

							<div class="setting-row">
								<span class="setting-label">Background</span>
								<div class="segmented">
									{#each THEMES as theme (theme)}
										<button
											type="button"
											class={['segment theme', theme, readerSettings.theme === theme && 'active']}
											aria-pressed={readerSettings.theme === theme}
											onclick={() => updateSettings({ theme })}
										>
											{theme === 'sepia' ? 'Sepia' : theme === 'dark' ? 'Dark' : 'Light'}
										</button>
									{/each}
								</div>
							</div>

							<div class="setting-row">
								<span class="setting-label">Columns</span>
								<div class="segmented">
									<button
										type="button"
										class={['segment', readerSettings.columns === 1 && 'active']}
										aria-pressed={readerSettings.columns === 1}
										onclick={() => updateSettings({ columns: 1 })}
									>
										Single
									</button>
									<button
										type="button"
										class={['segment', readerSettings.columns === 2 && 'active']}
										aria-pressed={readerSettings.columns === 2}
										onclick={() => updateSettings({ columns: 2 })}
									>
										Double
									</button>
								</div>
							</div>
						</div>
					{/if}
				</div>
			{/if}

			{#if isComic}
				<div class="popover-anchor">
					<button
						type="button"
						class="fs-button"
						onclick={() => {
							comicSettingsOpen = !comicSettingsOpen;
							settingsOpen = false;
							tocOpen = false;
						}}
						aria-label="Comic settings"
						aria-expanded={comicSettingsOpen}
						title="Comic settings"
					>
						<Settings2 size={18} />
					</button>
					{#if comicSettingsOpen}
						<div class="popover settings-popover" role="dialog" aria-label="Comic settings">
							<div class="popover-heading">Comic settings</div>
							<div class="setting-row">
								<span class="setting-label">Direction</span>
								<div class="segmented">
									<button
										type="button"
										class={['segment', comicSettings.direction === 'ltr' && 'active']}
										aria-pressed={comicSettings.direction === 'ltr'}
										onclick={() => updateComicSettings({ direction: 'ltr' })}
									>
										L → R
									</button>
									<button
										type="button"
										class={['segment', comicSettings.direction === 'rtl' && 'active']}
										aria-pressed={comicSettings.direction === 'rtl'}
										onclick={() => updateComicSettings({ direction: 'rtl' })}
									>
										R → L
									</button>
								</div>
							</div>
							<div class="setting-row">
								<span class="setting-label">Pages</span>
								<div class="segmented">
									<button
										type="button"
										class={['segment', comicSettings.pageMode === 'single' && 'active']}
										aria-pressed={comicSettings.pageMode === 'single'}
										onclick={() => updateComicSettings({ pageMode: 'single' })}
									>
										Single
									</button>
									<button
										type="button"
										class={['segment', comicSettings.pageMode === 'double' && 'active']}
										aria-pressed={comicSettings.pageMode === 'double'}
										onclick={() => updateComicSettings({ pageMode: 'double' })}
									>
										Double
									</button>
								</div>
							</div>
						</div>
					{/if}
				</div>
			{/if}

			<button
				type="button"
				class="fs-button"
				onclick={toggleNativeFullscreen}
				aria-label={isNativeFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
				title={isNativeFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
			>
				{#if isNativeFullscreen}
					<Minimize size={18} />
				{:else}
					<Maximize size={18} />
				{/if}
			</button>
		</div>
	</header>

	<div class="reader-viewport">
		{#if !format}
			<p class="muted">This book has no readable format.</p>
		{:else if isEpub && fileUrl && EpubReader}
			<EpubReader
				bookUrl={fileUrl}
				initialCfi={progress && !progress.completed ? progress.locator : null}
				onRelocate={handleEpubRelocate}
				onToggleHeader={toggleChrome}
				onPageTurned={hideChrome}
				chromeVisible={chromeVisible}
				settings={readerSettings}
				onToc={(toc) => (epubToc = toc)}
				bind:goToChapter={epubGoToChapter}
			/>
		{:else if isComic && ComicReader}
			<ComicReader
				bookId={bookId}
				pageCount={data.book.page_count ?? 0}
				startPage={parsePage(progress?.locator)}
				onPageChange={handleComicPage}
				onToggleHeader={toggleChrome}
				onPageTurned={hideChrome}
				chromeVisible={chromeVisible}
				settings={comicSettings}
			/>
		{:else if isPdf && fileUrl && PdfReader}
			<PdfReader
				fileUrl={fileUrl}
				initialLocator={progress?.locator ?? null}
				onPageChange={handlePdfPage}
			/>
		{:else}
			<p class="muted">Preparing reader…</p>
		{/if}
	</div>
</div>

<style>
	.book-reader-container {
		position: relative;
		display: flex;
		flex-direction: column;
		height: 100%;
		width: 100%;
		background: var(--bg);
		overflow: hidden;
	}

	/* Positioning and fade live in the shared chrome stylesheet. Like the video
	   player, the header drops the gradient in favour of frosted pills. It floats
	   above the reader so the viewport never resizes and EPUB text never reflows. */
	.reader-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		padding: 1rem 1.5rem;
		background: none;
	}

	.reader-header-left {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		min-width: 0;
	}

	.back-btn,
	.fs-button {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2.5rem;
		height: 2.5rem;
		border-radius: 50%;
		background: rgba(15, 18, 24, 0.58);
		border: none;
		color: #fff;
		cursor: pointer;
		text-decoration: none;
		flex-shrink: 0;
		backdrop-filter: blur(8px);
		transition:
			background 0.15s ease,
			color 0.15s ease;
	}

	.back-btn:hover,
	.fs-button:hover {
		background: rgba(255, 255, 255, 0.2);
		color: var(--accent);
		text-decoration: none;
	}

	.crumbs {
		font-size: 1.05rem;
		font-weight: 600;
		color: #fff;
		margin: 0;
		padding: 0.4rem 0.9rem;
		border-radius: 9999px;
		background: rgba(15, 18, 24, 0.58);
		backdrop-filter: blur(8px);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.reader-header-right {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		flex-shrink: 0;
	}

	.popover-anchor {
		position: relative;
		display: inline-flex;
	}

	.popover {
		position: absolute;
		top: calc(100% + 0.5rem);
		right: 0;
		z-index: 30;
		width: max-content;
		max-width: min(22rem, 85vw);
		padding: 0.4rem;
		background: rgba(14, 17, 22, 0.96);
		border: 1px solid var(--border);
		border-radius: 10px;
		box-shadow: 0 8px 24px rgba(0, 0, 0, 0.55);
		backdrop-filter: blur(10px);
	}

	.popover-heading {
		padding: 0.25rem 0.5rem 0.5rem;
		font-size: 0.75rem;
		font-weight: 700;
		letter-spacing: 0.05em;
		text-transform: uppercase;
		color: var(--muted);
	}

	.popover-scroll {
		max-height: 60vh;
		overflow-y: auto;
	}

	.popover-item,
	.popover-empty {
		display: block;
		width: 100%;
		padding: 0.45rem 0.6rem;
		border-radius: calc(var(--radius) * 0.6);
		text-align: left;
		color: var(--text);
		font-size: 0.9rem;
	}

	.popover-item {
		background: transparent;
		border: 0;
		cursor: pointer;
	}

	.popover-item:hover {
		background: var(--surface-2);
	}

	.popover-empty {
		margin: 0;
		color: var(--muted);
	}

	.settings-popover {
		width: 17rem;
	}

	.setting-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding: 0.35rem 0.5rem;
	}

	.setting-label {
		font-size: 0.85rem;
		color: var(--muted);
	}

	.segmented {
		display: inline-flex;
		gap: 0.25rem;
	}

	.stepper {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
	}

	.step {
		width: 2rem;
		height: 1.9rem;
		padding: 0;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: calc(var(--radius) * 0.6);
		color: var(--text);
		font-size: 1rem;
		line-height: 1;
		cursor: pointer;
	}

	.step:hover:not(:disabled) {
		border-color: var(--muted);
	}

	.step:disabled {
		opacity: 0.4;
		cursor: default;
	}

	.stepper-value {
		min-width: 2.5ch;
		text-align: center;
		font-size: 0.9rem;
		font-variant-numeric: tabular-nums;
	}

	.segment {
		min-width: 2.4rem;
		padding: 0.3rem 0.5rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: calc(var(--radius) * 0.6);
		color: var(--muted);
		font-size: 0.8rem;
		cursor: pointer;
	}

	.segment:hover {
		color: var(--text);
	}

	.segment.active {
		background: var(--accent);
		border-color: var(--accent);
		color: #fff;
	}

	.segment.theme.light {
		background: #ffffff;
		color: #1a1a1a;
	}

	.segment.theme.sepia {
		background: #f5ecd7;
		color: #4a3b2c;
	}

	.segment.theme.dark {
		background: #181a1b;
		color: #e6e6e6;
	}

	.reader-viewport {
		flex: 1;
		min-height: 0;
		width: 100%;
		display: flex;
		flex-direction: column;
		overflow: hidden;
		position: relative;
	}

	.muted {
		color: var(--muted);
		padding: 2rem;
		text-align: center;
	}
</style>
