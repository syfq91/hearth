<script lang="ts">
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();
</script>

<h1>Welcome to Hearth</h1>

{#if data.health}
	<section class="card" aria-label="Server health">
		<h2>Server health</h2>
		<dl>
			<div>
				<dt>Status</dt>
				<dd>{data.health.status}</dd>
			</div>
			<div>
				<dt>Version</dt>
				<dd>{data.health.version}</dd>
			</div>
			<div>
				<dt>ffmpeg</dt>
				<dd>{data.health.ffmpeg.status}{data.health.ffmpeg.version ? ` (${data.health.ffmpeg.version})` : ''}</dd>
			</div>
			<div>
				<dt>VAAPI</dt>
				<dd>{data.health.vaapi.status}</dd>
			</div>
			<div>
				<dt>Database</dt>
				<dd>{data.health.database.status}</dd>
			</div>
		</dl>
	</section>
{:else if data.error}
	<p class="error">Could not reach the API: {data.error}</p>
{/if}

<p class="muted">The full home screen arrives with Phase 9 task 9.3.</p>

<style>
	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 1rem 1.25rem;
		max-width: 32rem;
	}

	dl {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 0.35rem 1rem;
		margin: 0;
	}

	dl > div {
		display: contents;
	}

	dt {
		color: var(--muted);
	}

	dd {
		margin: 0;
	}

	.error {
		color: var(--danger);
	}

	.muted {
		color: var(--muted);
	}
</style>
