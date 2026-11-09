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
		User
	} from '@lucide/svelte';

	let { data, children }: LayoutProps = $props();

	let userLibraries = $state<LibrarySummary[]>([]);

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
		<header class="topbar">
			<a class="brand" href="/" data-sveltekit-reload>Hearth</a>
			<nav aria-label="Primary">
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
				<a href="/favorites" aria-current={isActive('/favorites') ? 'page' : undefined}>
					<Heart size={16} />
					<span>Favourites</span>
				</a>
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
		flex-wrap: wrap;
		align-items: center;
		gap: 0.6rem 1rem;
		padding: 0.75rem 1.25rem;
		background: var(--surface);
		border-bottom: 1px solid var(--border);
		position: sticky;
		top: 0;
		z-index: 10;
	}

	.brand {
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
		flex-wrap: wrap;
		align-items: center;
		gap: 0.85rem;
		font-size: 0.95rem;
	}

	nav a {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		color: var(--muted);
		padding: 0.25rem 0.25rem;
		text-decoration: none;
		font-weight: 500;
		border-radius: var(--radius);
		transition: color 0.15s ease;
	}

	nav a:hover {
		color: var(--text);
	}

	nav a[aria-current='page'] {
		color: var(--text);
		font-weight: 600;
	}

	.topbar-actions {
		display: flex;
		align-items: center;
		gap: 0.4rem;
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
		transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
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
		}

		nav {
			gap: 0.6rem;
			font-size: 0.9rem;
		}

		main {
			padding: 0.75rem;
		}
	}
</style>
