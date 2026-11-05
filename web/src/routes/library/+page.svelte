<script lang="ts">
	import type { PageProps } from './$types';
	import { Film, Tv, Music, BookOpen, Folder } from '@lucide/svelte';

	let { data }: PageProps = $props();

	const typeLabel: Record<string, string> = {
		movie: 'Movies',
		tv: 'Shows',
		series: 'Shows',
		music: 'Music',
		book: 'Books'
	};

	function getIcon(type: string) {
		switch (type) {
			case 'movie':
				return Film;
			case 'tv':
			case 'series':
				return Tv;
			case 'music':
				return Music;
			case 'book':
				return BookOpen;
			default:
				return Folder;
		}
	}
</script>

<svelte:head>
	<title>Libraries · Hearth</title>
</svelte:head>

<h1>Libraries</h1>

{#if data.libraries.length === 0}
	<p class="empty">No libraries yet. Create one in the admin area.</p>
	<a class="button" href="/admin/libraries">Set up libraries</a>
{:else}
	<ul class="grid">
		{#each data.libraries as library (library.id)}
			{@const Icon = getIcon(library.type)}
			<li>
				<a class="library" href={`/library/${library.id}`}>
					<div class="icon-wrap" aria-hidden="true">
						<Icon size={24} />
					</div>
					<span class="name">{library.name}</span>
					<span class="kind">{typeLabel[library.type] ?? library.type}</span>
				</a>
			</li>
		{/each}
	</ul>
{/if}

<style>
	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
		gap: 1rem;
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.library {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 1.25rem;
		height: 100%;
	}

	.library:hover {
		text-decoration: none;
		background: var(--surface-2);
	}

	.icon-wrap {
		color: var(--accent);
		display: flex;
		align-items: center;
		margin-bottom: 0.25rem;
	}

	.name {
		font-size: 1.1rem;
		font-weight: 600;
	}

	.kind {
		color: var(--muted);
		font-size: 0.85rem;
	}

	.empty {
		color: var(--muted);
	}

	.button {
		display: inline-block;
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		border-radius: var(--radius);
		padding: 0.6rem 1.2rem;
	}
</style>
