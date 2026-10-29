<script lang="ts">
	import { api } from '../../../lib/api/client';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let audioBitrate = $state<number | null>(null);
	let defaultMaxBitrate = $state<number | null>(null);
	let idleTimeout = $state<number | null>(null);
	let maxHw = $state<number | null>(null);
	let maxSw = $state<number | null>(null);
	let forceSoftware = $state(false);

	$effect(() => {
		const settings = data.settings;
		if (!settings) return;
		audioBitrate = settings.audio_bitrate_kbps ?? null;
		defaultMaxBitrate = settings.default_max_bitrate ?? null;
		idleTimeout = settings.idle_timeout_seconds ?? null;
		maxHw = settings.max_hw_sessions ?? null;
		maxSw = settings.max_sw_sessions ?? null;
		forceSoftware = settings.force_software;
	});

	let saving = $state(false);
	let message = $state<string | null>(null);
	let error = $state<string | null>(null);

	async function save(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		saving = true;
		message = null;
		error = null;
		try {
			await api.put('/admin/transcoding/settings', {
				body: {
					audio_bitrate_kbps: audioBitrate,
					default_max_bitrate: defaultMaxBitrate,
					idle_timeout_seconds: idleTimeout,
					max_hw_sessions: maxHw,
					max_sw_sessions: maxSw,
					force_software: forceSoftware
				}
			});
			message = 'Transcoding settings saved.';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			saving = false;
		}
	}

	async function stopSession(sessionId: string): Promise<void> {
		error = null;
		try {
			await api.post(`/admin/transcoding/sessions/${sessionId}/stop`);
			message = 'Stop requested.';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}
</script>

<svelte:head>
	<title>Transcoding · Admin · Hearth</title>
</svelte:head>

{#if data.error}
	<p class="err" role="alert">Some transcoding data failed to load: {data.error}</p>
{/if}

{#if data.capabilities}
	<section class="card" aria-labelledby="caps-heading">
		<h2 id="caps-heading">Capabilities</h2>
		<dl class="facts">
			<div>
				<dt>Available</dt>
				<dd>{data.capabilities.available ? 'yes' : 'no'}</dd>
			</div>
			<div>
				<dt>Render device</dt>
				<dd>{data.capabilities.render_device ?? '—'}</dd>
			</div>
			<div>
				<dt>Driver</dt>
				<dd>{data.capabilities.driver_version ?? '—'}</dd>
			</div>
			<div>
				<dt>Software encoders</dt>
				<dd>{(data.capabilities.software_encoders ?? []).join(', ') || '—'}</dd>
			</div>
			{#if data.capabilities.error}
				<div>
					<dt>Error</dt>
					<dd class="err">{data.capabilities.error}</dd>
				</div>
			{/if}
		</dl>
	</section>
{/if}

{#if data.settings}
	<section class="card" aria-labelledby="settings-heading">
		<h2 id="settings-heading">Settings</h2>
		<form class="grid" onsubmit={save}>
			<label>
				<span>Audio bitrate (kbps)</span>
				<input type="number" min={64} max={512} bind:value={audioBitrate} />
			</label>
			<label>
				<span>Default max bitrate (bps, blank = unlimited)</span>
				<input type="number" min={500000} bind:value={defaultMaxBitrate} />
			</label>
			<label>
				<span>Idle timeout (seconds)</span>
				<input type="number" min={10} max={600} bind:value={idleTimeout} />
			</label>
			<label>
				<span>Max hardware sessions</span>
				<input type="number" min={1} max={32} bind:value={maxHw} />
			</label>
			<label>
				<span>Max software sessions</span>
				<input type="number" min={1} max={32} bind:value={maxSw} />
			</label>
			<label class="check">
				<input type="checkbox" bind:checked={forceSoftware} />
				<span>Force software encoding</span>
			</label>
			<button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save settings'}</button>
		</form>
	</section>
{/if}

{#if message}
	<p class="ok" role="status">{message}</p>
{/if}

<section class="card" aria-labelledby="sessions-heading">
	<h2 id="sessions-heading">Active sessions</h2>
	{#if data.sessions.length === 0}
		<p class="muted">No active transcoding sessions.</p>
	{:else}
		<ul class="list">
			{#each data.sessions as session (session.id)}
				<li class="row">
					<div>
						<strong>{session.mode}</strong>
						{#if session.is_hardware}<span class="chip">hw</span>{/if}
						<p class="muted">
							{session.fps ?? '?'} fps · {session.speed ?? '?'}× ·
							{Math.round((session.uptime_seconds ?? 0) / 60)}m ·
							{session.hwaccel ?? 'sw'}
						</p>
					</div>
					<button type="button" class="ghost danger" onclick={() => stopSession(session.id)}>
						Stop
					</button>
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

	.grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
		gap: 0.85rem;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.85rem;
		color: var(--muted);
	}

	.check {
		flex-direction: row;
		align-items: center;
		gap: 0.4rem;
	}

	input {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.45rem 0.6rem;
		color: var(--text);
		font: inherit;
	}

	button[type='submit'] {
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		border: 0;
		border-radius: var(--radius);
		padding: 0.5rem 1.1rem;
		cursor: pointer;
		align-self: end;
	}

	.list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 0.6rem;
	}

	.row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.65rem 0.85rem;
	}

	.chip {
		font-size: 0.72rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0 0.5rem;
		margin-left: 0.35rem;
	}

	.ghost {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.4rem 0.9rem;
		cursor: pointer;
		white-space: nowrap;
	}

	.danger {
		border-color: var(--danger);
		color: var(--danger);
	}

	.muted {
		color: var(--muted);
		font-size: 0.85rem;
		margin: 0.25rem 0 0;
	}

	.ok {
		color: var(--accent);
		font-size: 0.9rem;
	}

	.err {
		color: var(--danger);
		font-size: 0.9rem;
	}
</style>
