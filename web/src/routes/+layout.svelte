<script lang="ts">
	import '../app.css';
	import favicon from '#lib/assets/favicon.svg';
	import { logout, session } from '../lib/auth.svelte';
	import { api } from '../lib/api/client';
	import type { LibrarySummary } from '../lib/api/types';
	import { setTvMode, tv } from '../lib/tv.svelte';
	import { spatialFocus } from '../lib/actions/spatial-focus';
	import AudioBar from '../lib/components/AudioBar.svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { LayoutProps } from './$types';
	import {
		Film,
		Tv as TvIcon,
		Music,
		BookOpen,
		Folder,
		Heart,
		Search,
		Shield,
		LogOut,
		User,
		ChevronDown
	} from '@lucide/svelte';

	let { data, children }: LayoutProps = $props();

	let userLibraries = $state<LibrarySummary[]>([]);
	let hasFavorites = $state(false);

	$effect(() => {
		if (data.libraries && data.libraries.length > 0) {
			userLibraries = data.libraries;
		} else if (session.status === 'authenticated') {
			void api
				.get<LibrarySummary[]>('/libraries')
				.then((libs) => {
					userLibraries = libs;
				})
				.catch(() => {});
		} else {
			userLibraries = [];
		}

		if (typeof data.hasFavorites === 'boolean') {
			hasFavorites = data.hasFavorites;
		} else if (session.status === 'authenticated') {
			void api
				.get<{ items: unknown[] }>('/home/favorites', { query: { limit: 1 } })
				.then((res) => {
					hasFavorites = res.items.length > 0;
				})
				.catch(() => {});
		} else {
			hasFavorites = false;
		}
	});

	function getLibraryIcon(type: string) {
		switch (type) {
			case 'movie':
				return Film;
			case 'tv':
			case 'series':
				return TvIcon;
			case 'music':
				return Music;
			case 'book':
				return BookOpen;
			default:
				return Folder;
		}
	}

	interface LibraryGroup {
		type: string;
		label: string;
		icon: typeof Film;
		libraries: LibrarySummary[];
	}

	const groupedLibraries = $derived.by(() => {
		const groups: Record<string, LibrarySummary[]> = {};
		for (const lib of userLibraries) {
			const t = lib.type === 'series' ? 'tv' : lib.type;
			if (!groups[t]) groups[t] = [];
			groups[t].push(lib);
		}
		const order = [
			{ type: 'movie', label: 'Movies', icon: Film },
			{ type: 'tv', label: 'Shows', icon: TvIcon },
			{ type: 'music', label: 'Music', icon: Music },
			{ type: 'book', label: 'Books', icon: BookOpen }
		];
		const result: LibraryGroup[] = [];
		for (const def of order) {
			if (groups[def.type] && groups[def.type].length > 0) {
				result.push({
					...def,
					libraries: groups[def.type]
				});
			}
		}
		for (const [t, libs] of Object.entries(groups)) {
			if (!order.some((o) => o.type === t)) {
				result.push({
					type: t,
					label: t.charAt(0).toUpperCase() + t.slice(1),
					icon: Folder,
					libraries: libs
				});
			}
		}
		return result;
	});

	const isFullscreenMedia = $derived(
		page.url.pathname.startsWith('/watch/') ||
			/\/books\/[^/]+\/read\/?$/.test(page.url.pathname)
	);

	let topbarRef = $state<HTMLElement | null>(null);
	let navRef = $state<HTMLElement | null>(null);
	let isCombined = $state(false);
	let minUncombinedWidth = $state(0);

	function checkHeaderWidth() {
		if (typeof window === 'undefined') return;
		if (!topbarRef || !navRef) return;

		const brandEl = topbarRef.querySelector('.brand') as HTMLElement | null;
		const actionsEl = topbarRef.querySelector('.topbar-actions') as HTMLElement | null;
		const brandW = brandEl ? brandEl.offsetWidth : 80;
		const actionsW = actionsEl ? actionsEl.offsetWidth : 80;
		const availableForNav = Math.max(0, topbarRef.clientWidth - brandW - actionsW - 64);

		if (!isCombined) {
			if (navRef.scrollWidth > availableForNav) {
				minUncombinedWidth = topbarRef.clientWidth + (navRef.scrollWidth - availableForNav) + 20;
				isCombined = true;
			}
		} else {
			if (minUncombinedWidth > 0 && topbarRef.clientWidth >= minUncombinedWidth + 15) {
				isCombined = false;
			}
		}
	}

	$effect(() => {
		const _ = userLibraries;
		minUncombinedWidth = 0;
		isCombined = false;
		if (typeof window !== 'undefined') {
			requestAnimationFrame(() => {
				checkHeaderWidth();
			});
		}
	});

	$effect(() => {
		if (!topbarRef) return;
		const ro = new ResizeObserver(() => {
			checkHeaderWidth();
		});
		ro.observe(topbarRef);
		window.addEventListener('resize', checkHeaderWidth);
		return () => {
			ro.disconnect();
			window.removeEventListener('resize', checkHeaderWidth);
		};
	});

	let userMenuOpen = $state(false);
	let userMenuRef = $state<HTMLElement | null>(null);
	let openLibDropdown = $state<string | null>(null);

	function toggleUserMenu() {
		userMenuOpen = !userMenuOpen;
		if (userMenuOpen) openLibDropdown = null;
	}

	function closeUserMenu() {
		userMenuOpen = false;
	}

	function toggleLibDropdown(type: string) {
		openLibDropdown = openLibDropdown === type ? null : type;
		if (openLibDropdown) userMenuOpen = false;
	}

	$effect(() => {
		// Close menus on route navigation
		const _ = page.url.pathname;
		userMenuOpen = false;
		openLibDropdown = null;
	});

	$effect(() => {
		if (!userMenuOpen && !openLibDropdown) return;
		function handleOutside(event: MouseEvent) {
			const target = event.target as Node;
			if (userMenuRef && !userMenuRef.contains(target)) {
				userMenuOpen = false;
			}
			if (navRef && !navRef.contains(target)) {
				openLibDropdown = null;
			}
		}
		function handleKeydown(event: KeyboardEvent) {
			if (event.key === 'Escape') {
				userMenuOpen = false;
				openLibDropdown = null;
			}
		}
		window.addEventListener('click', handleOutside);
		window.addEventListener('keydown', handleKeydown);
		return () => {
			window.removeEventListener('click', handleOutside);
			window.removeEventListener('keydown', handleKeydown);
		};
	});

	function isActive(path: string): boolean {
		return path === '/' ? page.url.pathname === '/' : page.url.pathname.startsWith(path);
	}

	function isGroupActive(group: LibraryGroup): boolean {
		return group.libraries.some((lib) => isActive(`/library/${lib.id}`));
	}

	async function signOut() {
		await logout();
		await goto('/login');
	}

	// TV mode scales the whole document (all sizing is rem-based) and enables
	// D-pad spatial navigation; the class lives on <html> so tokens and type
	// scale apply everywhere, including lazily loaded reader/player chunks.
	$effect(() => {
		document.documentElement.classList.toggle('tv', tv.on);
		return () => document.documentElement.classList.remove('tv');
	});
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>Hearth</title>
</svelte:head>

<div class="app" class:fullscreen-app={isFullscreenMedia} use:spatialFocus>
	{#if !isFullscreenMedia}
		<a class="skip-link" href="#main">Skip to content</a>
		<header class="topbar" bind:this={topbarRef}>
			<a class="brand" href="/" data-sveltekit-reload>Hearth</a>
			<nav aria-label="Primary" bind:this={navRef}>
				{#if isCombined}
					{#each groupedLibraries as group (group.type)}
						{@const Icon = group.icon}
						{@const active = isGroupActive(group)}
						{@const open = openLibDropdown === group.type}
						{#if group.libraries.length === 1}
							{@const lib = group.libraries[0]}
							<a
								href={`/library/${lib.id}`}
								aria-current={isActive(`/library/${lib.id}`) ? 'page' : undefined}
							>
								<Icon size={16} />
								<span>{lib.name}</span>
							</a>
						{:else}
							<div class="lib-dropdown-wrapper">
								<button
									type="button"
									class="nav-dropdown-btn"
									class:active
									aria-expanded={open}
									aria-haspopup="menu"
									onclick={(e) => {
										e.stopPropagation();
										toggleLibDropdown(group.type);
									}}
								>
									<Icon size={16} />
									<span>{group.label}</span>
									<span class="chevron"><ChevronDown size={14} /></span>
								</button>
								{#if open}
									<div class="dropdown-menu lib-menu" role="menu">
										{#each group.libraries as lib (lib.id)}
											<a
												class="dropdown-item"
												href={`/library/${lib.id}`}
												role="menuitem"
												aria-current={isActive(`/library/${lib.id}`) ? 'page' : undefined}
												onclick={() => {
													openLibDropdown = null;
												}}
											>
												<span>{lib.name}</span>
											</a>
										{/each}
									</div>
								{/if}
							</div>
						{/if}
					{/each}
				{:else}
					{#each userLibraries as lib (lib.id)}
						{@const Icon = getLibraryIcon(lib.type)}
						<a
							href={`/library/${lib.id}`}
							aria-current={isActive(`/library/${lib.id}`) ? 'page' : undefined}
						>
							<Icon size={16} />
							<span>{lib.name}</span>
						</a>
					{/each}
				{/if}
				{#if hasFavorites}
					<a href="/favorites" aria-current={isActive('/favorites') ? 'page' : undefined}>
						<Heart size={16} />
						<span>Favourites</span>
					</a>
				{/if}
			</nav>
			<div class="spacer"></div>
			<div class="topbar-actions">
				<a
					href="/search"
					class="icon-btn"
					aria-current={isActive('/search') ? 'page' : undefined}
					aria-label="Search"
					title="Search"
				>
					<Search size={18} />
				</a>
				{#if session.status === 'authenticated' && session.user}
					<div class="user-menu-wrapper" bind:this={userMenuRef}>
						<button
							type="button"
							class="icon-btn"
							onclick={toggleUserMenu}
							aria-expanded={userMenuOpen}
							aria-haspopup="menu"
							aria-label={session.user.username}
							title={session.user.username}
						>
							<User size={18} />
						</button>

						{#if userMenuOpen}
							<div class="dropdown-menu" role="menu">
								<div class="user-dropdown-header">
									<span class="user-dropdown-name">{session.user.username}</span>
									<span class="user-dropdown-role">{session.user.role}</span>
								</div>

								<div class="dropdown-divider"></div>

								<a
									class="dropdown-item"
									href="/profile"
									role="menuitem"
									onclick={closeUserMenu}
								>
									<User size={15} />
									<span>Profile</span>
								</a>

								{#if session.user.role === 'admin'}
									<a
										class="dropdown-item"
										href="/admin"
										role="menuitem"
										aria-current={isActive('/admin') ? 'page' : undefined}
										onclick={closeUserMenu}
									>
										<Shield size={15} />
										<span>Admin</span>
									</a>
								{/if}

								<button
									type="button"
									class="dropdown-item"
									role="menuitem"
									onclick={() => {
										setTvMode(!tv.on);
									}}
								>
									<TvIcon size={15} />
									<span>TV mode</span>
									<span class="dropdown-item-state">{tv.on ? 'On' : 'Off'}</span>
								</button>

								<div class="dropdown-divider"></div>

								<button
									type="button"
									class="dropdown-item danger"
									role="menuitem"
									onclick={() => {
										closeUserMenu();
										void signOut();
									}}
								>
									<LogOut size={15} />
									<span>Sign out</span>
								</button>
							</div>
						{/if}
					</div>
				{/if}
			</div>
		</header>
	{/if}
	<main id="main" tabindex="-1" class:fullscreen-main={isFullscreenMedia}>
		{@render children()}
	</main>
	{#if !isFullscreenMedia}
		<AudioBar />
	{/if}
</div>

<style>
	.app {
		display: flex;
		flex-direction: column;
		min-height: 100vh;
	}

	.app.fullscreen-app {
		height: 100vh;
		min-height: 100vh;
		overflow: hidden;
	}

	.topbar {
		display: flex;
		flex-wrap: nowrap;
		align-items: center;
		gap: 0.75rem 1rem;
		padding: 0.75rem 1.25rem;
		background: var(--surface);
		border-bottom: 1px solid var(--border);
		position: sticky;
		top: 0;
		z-index: 10;
		min-width: 0;
	}

	.brand {
		flex-shrink: 0;
		font-weight: 700;
		font-size: 1.15rem;
		letter-spacing: 0.02em;
		color: var(--accent);
		text-decoration: none;
		margin-right: 0.5rem;
	}

	.brand:hover {
		filter: brightness(1.1);
	}

	.spacer {
		flex: 1;
		min-width: 0.5rem;
	}

	/* Skip link: first in the tab order, visible only once focused. */
	.skip-link {
		position: absolute;
		left: 0.75rem;
		top: -4rem;
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		padding: 0.5rem 1.1rem;
		border-radius: var(--radius);
		z-index: 100;
		transition: top 0.15s ease;
	}

	.skip-link:focus {
		top: 0.75rem;
	}

	nav {
		display: flex;
		flex-wrap: nowrap;
		align-items: center;
		gap: 0.6rem;
		font-size: 0.95rem;
		min-width: 0;
	}

	nav a {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		color: var(--muted);
		padding: 0.25rem 0.35rem;
		text-decoration: none;
		font-weight: 500;
		border-radius: var(--radius);
		white-space: nowrap;
		flex-shrink: 0;
		transition:
			color 0.15s ease,
			background 0.15s ease;
	}

	nav a:hover {
		color: var(--text);
	}

	nav a[aria-current='page'] {
		color: var(--text);
		font-weight: 600;
	}

	.lib-dropdown-wrapper {
		position: relative;
		display: inline-flex;
		align-items: center;
		flex-shrink: 0;
	}

	.nav-dropdown-btn {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		color: var(--muted);
		background: transparent;
		border: 1px solid transparent;
		padding: 0.25rem 0.45rem;
		border-radius: var(--radius);
		font-size: 0.95rem;
		font-weight: 500;
		cursor: pointer;
		white-space: nowrap;
		transition:
			color 0.15s ease,
			background 0.15s ease,
			border-color 0.15s ease;
	}

	.nav-dropdown-btn:hover,
	.nav-dropdown-btn[aria-expanded='true'] {
		color: var(--text);
		background: var(--surface-2);
		border-color: var(--border);
	}

	.nav-dropdown-btn.active {
		color: var(--text);
		font-weight: 600;
	}

	.chevron {
		display: inline-flex;
		align-items: center;
		color: var(--muted);
		opacity: 0.7;
	}

	.lib-menu {
		min-width: 170px;
		left: 0;
		right: auto;
	}

	.topbar-actions {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		flex-shrink: 0;
		margin-left: auto;
		background: var(--surface);
		position: relative;
		z-index: 5;
	}

	.icon-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2.25rem;
		height: 2.25rem;
		border-radius: var(--radius);
		color: var(--muted);
		background: transparent;
		border: 1px solid transparent;
		cursor: pointer;
		text-decoration: none;
		transition:
			background 0.15s ease,
			color 0.15s ease,
			border-color 0.15s ease;
	}

	.icon-btn:hover,
	.icon-btn[aria-expanded='true'] {
		background: var(--surface-2);
		color: var(--text);
		border-color: var(--border);
	}

	.icon-btn[aria-current='page'] {
		background: var(--surface-2);
		color: var(--accent);
		border-color: var(--border);
	}

	.user-menu-wrapper {
		position: relative;
		display: inline-flex;
		align-items: center;
	}

	.dropdown-menu {
		position: absolute;
		top: calc(100% + 0.4rem);
		right: 0;
		min-width: 180px;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: 0 4px 20px rgba(0, 0, 0, 0.25);
		padding: 0.35rem;
		display: flex;
		flex-direction: column;
		gap: 0.15rem;
		z-index: 100;
	}

	.user-dropdown-header {
		display: flex;
		flex-direction: column;
		padding: 0.4rem 0.65rem 0.3rem;
	}

	.user-dropdown-name {
		font-weight: 600;
		font-size: 0.9rem;
		color: var(--text);
	}

	.user-dropdown-role {
		font-size: 0.75rem;
		color: var(--muted);
		text-transform: capitalize;
	}

	.dropdown-item {
		display: flex;
		align-items: center;
		gap: 0.6rem;
		width: 100%;
		padding: 0.5rem 0.65rem;
		font-size: 0.85rem;
		color: var(--text);
		background: transparent;
		border: none;
		border-radius: calc(var(--radius) - 2px);
		cursor: pointer;
		text-align: left;
		text-decoration: none;
		box-sizing: border-box;
	}

	.dropdown-item:hover {
		background: var(--surface-2);
	}

	.dropdown-item[aria-current='page'] {
		color: var(--accent);
		font-weight: 600;
	}

	.dropdown-item-state {
		margin-left: auto;
		color: var(--muted);
		font-size: 0.75rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		padding: 0.1rem 0.45rem;
		border-radius: 999px;
	}

	.dropdown-item.danger {
		color: #e57373;
	}

	.dropdown-item.danger:hover {
		background: rgba(229, 115, 115, 0.12);
	}

	.dropdown-divider {
		height: 1px;
		background: var(--border);
		margin: 0.25rem 0;
	}

	main {
		flex: 1;
		padding: 1.25rem;
		max-width: 1400px;
		width: 100%;
		margin: 0 auto;
		box-sizing: border-box;
	}

	main.fullscreen-main {
		padding: 0;
		max-width: none;
		width: 100%;
		height: 100%;
		margin: 0;
		display: flex;
		flex-direction: column;
		overflow: hidden;
	}

	@media (max-width: 720px) {
		.topbar {
			padding: 0.6rem 0.75rem;
			gap: 0.5rem;
		}

		nav {
			gap: 0.4rem;
			font-size: 0.9rem;
		}

		main {
			padding: 0.75rem;
		}
	}

	@media (max-width: 600px) {
		.topbar {
			padding: 0.5rem 0.6rem;
			gap: 0.35rem;
		}

		nav {
			gap: 0.25rem;
			font-size: 0.85rem;
		}

		.nav-dropdown-btn span,
		nav a span {
			display: none;
		}

		.chevron {
			display: none;
		}

		.nav-dropdown-btn,
		nav a {
			padding: 0.35rem;
		}
	}
</style>
