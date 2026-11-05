<script lang="ts">
	import { page } from '$app/state';
	import type { LayoutProps } from './$types';
	import { LayoutDashboard, FolderTree, Users, FileQuestion, Cpu } from '@lucide/svelte';

	let { children }: LayoutProps = $props();

	function isActive(path: string): boolean {
		return path === '/admin'
			? page.url.pathname === '/admin'
			: page.url.pathname.startsWith(path);
	}
</script>

<h1>Admin</h1>

<nav class="tabs" aria-label="Admin sections">
	<a href="/admin" aria-current={isActive('/admin') ? 'page' : undefined}>
		<LayoutDashboard size={15} /> <span>Overview</span>
	</a>
	<a
		href="/admin/libraries"
		aria-current={isActive('/admin/libraries') ? 'page' : undefined}
	>
		<FolderTree size={15} /> <span>Libraries</span>
	</a>
	<a href="/admin/users" aria-current={isActive('/admin/users') ? 'page' : undefined}>
		<Users size={15} /> <span>Users</span>
	</a>
	<a href="/admin/unmatched" aria-current={isActive('/admin/unmatched') ? 'page' : undefined}>
		<FileQuestion size={15} /> <span>Unmatched</span>
	</a>
	<a
		href="/admin/transcoding"
		aria-current={isActive('/admin/transcoding') ? 'page' : undefined}
	>
		<Cpu size={15} /> <span>Transcoding</span>
	</a>
</nav>

{@render children()}

<style>
	.tabs {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem;
		border-bottom: 1px solid var(--border);
		margin-bottom: 1.5rem;
	}

	.tabs a {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		color: var(--muted);
		padding: 0.5rem 0.9rem;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
	}

	.tabs a:hover {
		color: var(--text);
		text-decoration: none;
	}

	.tabs a[aria-current='page'] {
		color: var(--text);
		font-weight: 600;
		border-bottom-color: var(--accent);
	}
</style>
