<script module lang="ts">
	export type MusicSection = 'albums' | 'artists' | 'songs' | 'genres';
</script>

<script lang="ts">
	let { current }: { current: MusicSection } = $props();

	const sections: { id: MusicSection; label: string; href: string }[] = [
		{ id: 'albums', label: 'Albums', href: '/music' },
		{ id: 'artists', label: 'Artists', href: '/music/artists' },
		{ id: 'songs', label: 'Songs', href: '/music/songs' },
		{ id: 'genres', label: 'Genres', href: '/music/genres' }
	];
</script>

<nav class="tabs" aria-label="Music sections">
	{#each sections as section (section.id)}
		<a href={section.href} aria-current={current === section.id ? 'page' : undefined}>
			{section.label}
		</a>
	{/each}
</nav>

<style>
	.tabs {
		display: flex;
		gap: 0.25rem;
		border-bottom: 1px solid var(--border);
		margin-bottom: 1.25rem;
	}

	.tabs a {
		color: var(--muted);
		padding: 0.4rem 0.9rem;
		border-bottom: 2px solid transparent;
		margin-bottom: -1px;
	}

	.tabs a[aria-current='page'] {
		color: var(--text);
		font-weight: 600;
		border-bottom-color: var(--accent);
	}
</style>
