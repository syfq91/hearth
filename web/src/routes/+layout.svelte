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

	let { children }: LayoutProps = $props();

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

<div class="app" use:spatialFocus>
	<header class="topbar">
		<a class="brand" href="/" data-sveltekit-reload>Hearth</a>
		<nav aria-label="Primary">
			<a href="/" aria-current={isActive('/') ? 'page' : undefined}>Home</a>
			<a href="/library" aria-current={isActive('/library') ? 'page' : undefined}>Library</a>
			<a href="/music" aria-current={isActive('/music') ? 'page' : undefined}>Music</a>
			<a href="/books" aria-current={isActive('/books') ? 'page' : undefined}>Books</a>
			<a href="/search" aria-current={isActive('/search') ? 'page' : undefined}>Search</a>
			{#if session.status === 'authenticated' && session.user?.role === 'admin'}
				<a href="/admin" aria-current={isActive('/admin') ? 'page' : undefined}>Admin</a>
			{/if}
		</nav>
		<div class="spacer"></div>
		<button
			type="button"
			class="ghost tv-toggle"
			aria-pressed={tv.on}
			title="10-foot mode: larger text and D-pad navigation"
			onclick={() => setTvMode(!tv.on)}
		>
			TV mode
		</button>
		{#if session.status === 'authenticated' && session.user}
			<a
				class="user"
				href="/profile"
				title={session.user.role}
				aria-current={isActive('/profile') ? 'page' : undefined}
			>
				{session.user.username}
				{#if session.user.role === 'admin'}<span class="badge">admin</span>{/if}
			</a>
			<button type="button" class="ghost" onclick={signOut}>Sign out</button>
		{/if}
	</header>
	<main id="main" tabindex="-1">
		{@render children()}
	</main>
	<AudioBar />
</div>

<style>
	.app {
		display: flex;
		flex-direction: column;
		min-height: 100vh;
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
	}

	.spacer {
		flex: 1;
	}

	nav {
		display: flex;
		flex-wrap: wrap;
		gap: 0.9rem;
		font-size: 0.95rem;
	}

	nav a {
		color: var(--muted);
		padding: 0.25rem 0.15rem;
	}

	nav a:hover {
		color: var(--text);
	}

	nav a[aria-current='page'] {
		color: var(--text);
		font-weight: 600;
	}

	.user {
		color: var(--muted);
		font-size: 0.9rem;
	}

	.badge {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0 0.5rem;
		font-size: 0.75rem;
		margin-left: 0.35rem;
	}

	.ghost {
		background: transparent;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.35rem 0.75rem;
		cursor: pointer;
	}

	.ghost:hover {
		background: var(--surface-2);
	}

	.tv-toggle[aria-pressed='true'] {
		background: var(--accent);
		color: var(--accent-contrast);
		border-color: var(--accent);
		font-weight: 600;
	}

	main {
		flex: 1;
		padding: 1.25rem;
		max-width: 1400px;
		width: 100%;
		margin: 0 auto;
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
