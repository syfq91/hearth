<script lang="ts">
	import { page } from '$app/state';
</script>

<svelte:head>
	<title>{page.status} · Hearth</title>
</svelte:head>

<div class="error-container">
	<h1>{page.status}</h1>
	<p class="message">{page.error?.message || 'An unexpected error occurred'}</p>
	<div class="actions">
		<a href="/" class="home-link">Go to Home</a>
		<a href="/books" class="books-link">Back to Books</a>
	</div>
</div>

<style>
	.error-container {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		min-height: 50vh;
		text-align: center;
		gap: 1rem;
		padding: 2rem;
	}

	h1 {
		font-size: 3rem;
		margin: 0;
		color: var(--accent);
	}

	.message {
		color: var(--muted);
		max-width: 500px;
		line-height: 1.5;
	}

	.actions {
		display: flex;
		gap: 1rem;
		margin-top: 1rem;
	}

	.home-link,
	.books-link {
		padding: 0.5rem 1.25rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		color: var(--text);
		text-decoration: none;
	}

	.home-link:hover,
	.books-link:hover {
		background: var(--surface);
	}
</style>
