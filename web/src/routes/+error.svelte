<script lang="ts">
	import { page } from '$app/state';
	import type { LibrarySummary } from '../lib/api/types';

	const libraries = $derived<LibrarySummary[]>(
		(page.data?.libraries as LibrarySummary[] | undefined) ?? []
	);

	const backTarget = $derived.by(() => {
		// 1. Explicit ?lib= query parameter
		const queryLibId = page.url.searchParams.get('lib') || page.url.searchParams.get('library_id');
		if (queryLibId) {
			const lib = libraries.find((l) => l.id === queryLibId);
			if (lib) return { label: `Back to ${lib.name}`, href: `/library/${lib.id}` };
			return { label: 'Back to Library', href: `/library/${queryLibId}` };
		}

		// 2. Library page itself (/library/[libraryId])
		const path = page.url.pathname;
		const libMatch = path.match(/^\/library\/([^/]+)/);
		if (libMatch) {
			const lib = libraries.find((l) => l.id === libMatch[1]);
			if (lib) return { label: `Back to ${lib.name}`, href: `/library/${lib.id}` };
			return { label: 'Back to Libraries', href: '/library' };
		}

		// 3. Document referrer if from a library page
		if (typeof document !== 'undefined' && document.referrer) {
			try {
				const refUrl = new URL(document.referrer);
				const refMatch = refUrl.pathname.match(/^\/library\/([^/]+)/);
				if (refMatch) {
					const lib = libraries.find((l) => l.id === refMatch[1]);
					if (lib) return { label: `Back to ${lib.name}`, href: `/library/${lib.id}` };
				}
			} catch {
				/* ignore invalid referrer */
			}
		}

		// 4. Infer library by route type
		if (path.startsWith('/movies')) {
			const movieLib = libraries.find((l) => l.type === 'movie');
			if (movieLib) return { label: `Back to ${movieLib.name}`, href: `/library/${movieLib.id}` };
			return { label: 'Back to Movies', href: '/library' };
		}
		if (path.startsWith('/series') || path.startsWith('/episodes')) {
			const tvLib = libraries.find((l) => l.type === 'tv' || l.type === 'series');
			if (tvLib) return { label: `Back to ${tvLib.name}`, href: `/library/${tvLib.id}` };
			return { label: 'Back to Shows', href: '/library' };
		}
		if (path.startsWith('/books')) {
			const bookLibs = libraries.filter((l) => l.type === 'book');
			if (bookLibs.length === 1) {
				return { label: `Back to ${bookLibs[0].name}`, href: `/library/${bookLibs[0].id}` };
			}
			return { label: 'Back to Books', href: '/books' };
		}
		if (path.startsWith('/music')) {
			const musicLib = libraries.find((l) => l.type === 'music');
			if (musicLib) return { label: `Back to ${musicLib.name}`, href: `/library/${musicLib.id}` };
			return { label: 'Back to Music', href: '/music' };
		}

		// 5. /watch route fallback
		if (path.startsWith('/watch')) {
			const videoLibs = libraries.filter(
				(l) => l.type === 'movie' || l.type === 'tv' || l.type === 'series'
			);
			if (videoLibs.length === 1) {
				return { label: `Back to ${videoLibs[0].name}`, href: `/library/${videoLibs[0].id}` };
			}
			return { label: 'Back to Library', href: '/library' };
		}

		// 6. Generic fallback
		return { label: 'Back to Library', href: '/library' };
	});
</script>

<svelte:head>
	<title>{page.status} · Hearth</title>
</svelte:head>

<div class="error-container">
	<h1>{page.status}</h1>
	<p class="message">{page.error?.message || 'An unexpected error occurred'}</p>
	<div class="actions">
		<a href="/" class="home-link">Go to Home</a>
		<a href={backTarget.href} class="library-link">{backTarget.label}</a>
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
	.library-link {
		padding: 0.5rem 1.25rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		color: var(--text);
		text-decoration: none;
	}

	.home-link:hover,
	.library-link:hover {
		background: var(--surface);
	}
</style>
