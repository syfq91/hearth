<script lang="ts">
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const typeLabel: Record<string, string> = {
		movie: 'Movies',
		series: 'Shows',
		music: 'Music',
		book: 'Books'
	};
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
			<li>
				<a class="library" href={`/library/${library.id}`}>
					<span class="icon" aria-hidden="true">{typeLabel[library.type] ?? library.type}</span>
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

	.icon {
		font-size: 0.75rem;
		font-weight: 700;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--accent);
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
