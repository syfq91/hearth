import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

// The generated OpenAPI types live in packages/client (repo root, outside
// this package). They are consumed as type-only imports through the
// `@hearth/client` alias in tsconfig.json; the Vite alias and fs.allow entry
// below let Vite resolve the same file if it is ever loaded for real.
const hearthClient = new URL('../packages/client/src/index.ts', import.meta.url).pathname;

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// Static build served by FastAPI at `/` (SPA fallback), see app/main.py.
			adapter: adapter({
				pages: 'dist',
				assets: 'dist',
				fallback: 'index.html',
				strict: true
			})
		})
	],
	resolve: {
		alias: {
			'@hearth/client': hearthClient
		}
	},
	server: {
		fs: {
			allow: ['..']
		}
	},
	build: {
		// TV browsers ship Chromium M69+ (2020 models) through M130 (2026):
		// downlevel syntax so the bundle parses on older TV engines.
		target: 'es2019'
	}
});
