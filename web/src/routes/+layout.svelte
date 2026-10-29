<script lang="ts">
	import '../app.css';
	import favicon from '#lib/assets/favicon.svg';
	import { logout, session } from '../lib/auth.svelte';
	import { goto } from '$app/navigation';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	async function signOut() {
		await logout();
		await goto('/login');
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>Hearth</title>
</svelte:head>

<div class="app">
	<header class="topbar">
		<a class="brand" href="/" data-sveltekit-reload>Hearth</a>
		<nav aria-label="Primary">
			<!-- Screen links (library, music, books, search, admin) are added
			     with their routes in Phase 9 task 9.3. -->
		</nav>
		<div class="spacer"></div>
		{#if session.status === 'authenticated' && session.user}
			<span class="user" title={session.user.role}>
				{session.user.username}
				{#if session.user.role === 'admin'}<span class="badge">admin</span>{/if}
			</span>
			<button type="button" class="ghost" onclick={signOut}>Sign out</button>
		{/if}
	</header>
	<main id="main">
		{@render children()}
	</main>
</div>

<style>
	.app {
		display: flex;
		flex-direction: column;
		min-height: 100vh;
	}

	.topbar {
		display: flex;
		align-items: center;
		gap: 1rem;
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

	main {
		flex: 1;
		padding: 1.25rem;
		max-width: 1400px;
		width: 100%;
		margin: 0 auto;
	}
</style>
