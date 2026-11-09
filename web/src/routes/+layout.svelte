<script lang="ts">
	import '../app.css';
	import favicon from '#lib/assets/favicon.svg';
	import { logout, session } from '../lib/auth.svelte';
	import { setTvMode, tv } from '../lib/tv.svelte';
	import { spatialFocus } from '../lib/actions/spatial-focus';
	import AudioBar from '../lib/components/AudioBar.svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { LayoutProps } from './$types';
	import {
		House,
		Layers,
		Music,
		BookOpen,
		Heart,
		Search,
		Shield,
		Tv as TvIcon,
		LogOut,
		User,
		ChevronDown
	} from '@lucide/svelte';

	let { children }: LayoutProps = $props();

	const isFullscreenMedia = $derived(
		page.url.pathname.startsWith('/watch/') ||
			/\/books\/[^/]+\/read\/?$/.test(page.url.pathname)
	);

	let userMenuOpen = $state(false);
	let userMenuRef = $state<HTMLElement | null>(null);

	function toggleUserMenu() {
		userMenuOpen = !userMenuOpen;
	}

	function closeUserMenu() {
		userMenuOpen = false;
	}

	$effect(() => {
		// Close menu on route navigation
		const _ = page.url.pathname;
		userMenuOpen = false;
	});

	$effect(() => {
		if (!userMenuOpen) return;
		function handleOutside(event: MouseEvent) {
			if (userMenuRef && !userMenuRef.contains(event.target as Node)) {
				userMenuOpen = false;
			}
		}
		function handleKeydown(event: KeyboardEvent) {
			if (event.key === 'Escape') {
				userMenuOpen = false;
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
		<aside class="sidebar">
			<div class="sidebar-header">
				<a class="brand" href="/" data-sveltekit-reload>Hearth</a>
			</div>
			<nav class="sidebar-nav" aria-label="Primary">
				<a href="/" aria-current={isActive('/') ? 'page' : undefined}>
					<House size={18} />
					<span>Home</span>
				</a>
				<a href="/library" aria-current={isActive('/library') ? 'page' : undefined}>
					<Layers size={18} />
					<span>Library</span>
				</a>
				<a href="/music" aria-current={isActive('/music') ? 'page' : undefined}>
					<Music size={18} />
					<span>Music</span>
				</a>
				<a href="/books" aria-current={isActive('/books') ? 'page' : undefined}>
					<BookOpen size={18} />
					<span>Books</span>
				</a>
				<a href="/favorites" aria-current={isActive('/favorites') ? 'page' : undefined}>
					<Heart size={18} />
					<span>Favourites</span>
				</a>
			</nav>
		</aside>
		<div class="content-wrapper">
			<header class="topbar">
				<div class="spacer"></div>
				<a
					href="/search"
					class="search-btn"
					aria-current={isActive('/search') ? 'page' : undefined}
					aria-label="Search"
				>
					<Search size={16} />
					<span>Search</span>
				</a>
				{#if session.status === 'authenticated' && session.user}
					<div class="user-menu-wrapper" bind:this={userMenuRef}>
						<button
							type="button"
							class="user-btn"
							onclick={toggleUserMenu}
							aria-expanded={userMenuOpen}
							aria-haspopup="menu"
						>
							<User size={15} />
							<span>{session.user.username}</span>
							<span class="chevron" class:open={userMenuOpen}>
								<ChevronDown size={14} />
							</span>
						</button>

						{#if userMenuOpen}
							<div class="dropdown-menu" role="menu">
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
			</header>
			<main id="main" tabindex="-1">
				{@render children()}
			</main>
		</div>
		<AudioBar />
	{:else}
		<main id="main" tabindex="-1" class="fullscreen-main">
			{@render children()}
		</main>
	{/if}
</div>

<style>
	.app {
		display: flex;
		flex-direction: row;
		min-height: 100vh;
	}

	.app.fullscreen-app {
		height: 100vh;
		min-height: 100vh;
		overflow: hidden;
		flex-direction: column;
	}

	.sidebar {
		width: 220px;
		flex-shrink: 0;
		background: var(--surface);
		border-right: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		position: sticky;
		top: 0;
		height: 100vh;
		box-sizing: border-box;
		padding: 1.25rem 0.85rem;
		z-index: 10;
		overflow-y: auto;
	}

	.sidebar-header {
		padding: 0 0.5rem 1.25rem;
	}

	.brand {
		font-weight: 700;
		font-size: 1.25rem;
		letter-spacing: 0.02em;
		color: var(--accent);
		text-decoration: none;
	}

	.sidebar-nav {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}

	.sidebar-nav a {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		color: var(--muted);
		padding: 0.55rem 0.75rem;
		border-radius: var(--radius);
		font-size: 0.95rem;
		font-weight: 500;
		text-decoration: none;
		transition: background 0.15s ease, color 0.15s ease;
	}

	.sidebar-nav a:hover {
		background: var(--surface-2);
		color: var(--text);
	}

	.sidebar-nav a[aria-current='page'] {
		background: var(--surface-2);
		color: var(--accent);
		font-weight: 600;
	}

	.content-wrapper {
		display: flex;
		flex-direction: column;
		flex: 1;
		min-width: 0;
		min-height: 100vh;
	}

	.topbar {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.75rem 1.5rem;
		background: var(--surface);
		border-bottom: 1px solid var(--border);
		position: sticky;
		top: 0;
		z-index: 10;
	}

	.search-btn {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		color: var(--muted);
		padding: 0.35rem 0.65rem;
		border-radius: var(--radius);
		font-size: 0.9rem;
		font-weight: 500;
		text-decoration: none;
		border: 1px solid transparent;
		transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
	}

	.search-btn:hover {
		background: var(--surface-2);
		color: var(--text);
		border-color: var(--border);
	}

	.search-btn[aria-current='page'] {
		background: var(--surface-2);
		color: var(--accent);
		border-color: var(--border);
		font-weight: 600;
	}

	.spacer {
		flex: 1;
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

	.user-menu-wrapper {
		position: relative;
		display: inline-flex;
		align-items: center;
	}

	.user-btn {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		background: transparent;
		border: 1px solid transparent;
		border-radius: var(--radius);
		padding: 0.35rem 0.65rem;
		color: var(--text);
		font-size: 0.9rem;
		font-weight: 500;
		cursor: pointer;
		transition: background 0.15s ease, border-color 0.15s ease;
	}

	.user-btn:hover,
	.user-btn[aria-expanded='true'] {
		background: var(--surface-2);
		border-color: var(--border);
	}

	.chevron {
		display: inline-flex;
		align-items: center;
		color: var(--muted);
		transition: transform 0.2s ease;
	}

	.chevron.open {
		transform: rotate(180deg);
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
		padding: 1.5rem;
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

	@media (max-width: 768px) {
		.app {
			flex-direction: column;
		}

		.sidebar {
			width: 100%;
			height: auto;
			position: relative;
			border-right: none;
			border-bottom: 1px solid var(--border);
			padding: 0.6rem 0.75rem;
			flex-direction: row;
			align-items: center;
			justify-content: space-between;
			flex-wrap: wrap;
			gap: 0.5rem;
		}

		.sidebar-header {
			padding: 0 0.5rem;
		}

		.sidebar-nav {
			flex-direction: row;
			flex-wrap: wrap;
			gap: 0.25rem;
		}

		.sidebar-nav a {
			padding: 0.35rem 0.5rem;
			font-size: 0.85rem;
			gap: 0.4rem;
		}

		.topbar {
			padding: 0.6rem 0.75rem;
		}

		main {
			padding: 0.75rem;
		}
	}
</style>
