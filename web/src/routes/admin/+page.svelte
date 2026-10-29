<script lang="ts">
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	function formatBytes(bytes: number | null | undefined): string {
		if (!bytes) return '—';
		const units = ['B', 'KB', 'MB', 'GB', 'TB'];
		let value = bytes;
		let unit = 0;
		while (value >= 1024 && unit < units.length - 1) {
			value /= 1024;
			unit += 1;
		}
		return `${value.toFixed(1)} ${units[unit]}`;
	}

	function formatTime(value: string | null | undefined): string {
		return value ? new Date(value).toLocaleString() : '—';
	}
</script>

<svelte:head>
	<title>Admin · Hearth</title>
</svelte:head>

{#if data.error}
	<p class="banner" role="alert">Some admin data failed to load: {data.error}</p>
{/if}

{#if data.system}
	<section class="card" aria-labelledby="system-heading">
		<h2 id="system-heading">System</h2>
		<dl class="facts">
			<div>
				<dt>Platform</dt>
				<dd>{data.system.os_platform} {data.system.os_release}</dd>
			</div>
			<div>
				<dt>Python</dt>
				<dd>{data.system.python_version}</dd>
			</div>
			<div>
				<dt>CPU cores</dt>
				<dd>{data.system.cpu_count}</dd>
			</div>
			<div>
				<dt>Memory (RSS)</dt>
				<dd>{formatBytes(data.system.memory_rss_bytes)}</dd>
			</div>
			<div>
				<dt>Transcode capabilities</dt>
				<dd>
					{data.system.transcode_capabilities?.available
						? `available (${data.system.transcode_capabilities.render_device ?? 'device n/a'})`
						: 'unavailable'}
				</dd>
			</div>
		</dl>
	</section>
{/if}

<section class="card" aria-labelledby="sessions-heading">
	<h2 id="sessions-heading">Active transcodes</h2>
	{#if data.sessions.length === 0}
		<p class="muted">No active transcoding sessions.</p>
	{:else}
		<ul class="list">
			{#each data.sessions as session (session.id)}
				<li>
					<strong>{session.mode}</strong>
					{#if session.is_hardware}<span class="chip">hw</span>{/if}
					<span class="muted">
						· {session.fps ?? '?'} fps · speed {session.speed ?? '?'}× ·
						{Math.round((session.uptime_seconds ?? 0) / 60)}m
					</span>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<section class="card" aria-labelledby="jobs-heading">
	<h2 id="jobs-heading">Recent jobs</h2>
	{#if data.jobs.length === 0}
		<p class="muted">No jobs recorded.</p>
	{:else}
		<ul class="list">
			{#each data.jobs as job (job.id)}
				<li>
					<strong>{job.type}</strong>
					<span class="chip">{job.status}</span>
					<span class="muted">· {Math.round((job.progress ?? 0) * 100)}% · {formatTime(job.created_at)}</span>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<section class="card" aria-labelledby="audit-heading">
	<h2 id="audit-heading">Recent activity</h2>
	{#if data.audit.length === 0}
		<p class="muted">No audit entries.</p>
	{:else}
		<ul class="list">
			{#each data.audit as entry (entry.id)}
				<li>
					<strong>{entry.action}</strong>
					<span class="muted">
						· {entry.target_type ?? '—'} · {formatTime(entry.created_at)}
					</span>
				</li>
			{/each}
		</ul>
	{/if}
</section>

<style>
	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 1.25rem;
		margin-bottom: 1.25rem;
	}

	.card h2 {
		font-size: 1.05rem;
		margin-bottom: 0.75rem;
	}

	.facts {
		display: grid;
		grid-template-columns: auto 1fr;
		gap: 0.35rem 1rem;
		margin: 0;
	}

	.facts > div {
		display: contents;
	}

	dt {
		color: var(--muted);
	}

	dd {
		margin: 0;
	}

	.list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 0.4rem;
		font-size: 0.9rem;
	}

	.chip {
		font-size: 0.75rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0 0.5rem;
		margin-left: 0.35rem;
	}

	.muted {
		color: var(--muted);
	}

	.banner {
		background: var(--surface);
		border: 1px solid var(--border);
		border-left: 3px solid var(--danger);
		border-radius: var(--radius);
		padding: 0.6rem 1rem;
		color: var(--muted);
		margin-bottom: 1rem;
	}
</style>
