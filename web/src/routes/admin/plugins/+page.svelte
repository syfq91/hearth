<script lang="ts">
	import { api } from '../../../lib/api/client';
	import { invalidateAll } from '$app/navigation';
	import type { PluginSummary, PluginConfigResponse } from '../../../lib/api/types';
	import type { PageProps } from './$types';
	import PluginConfigModal from '../../../lib/components/PluginConfigModal.svelte';
	import { RefreshCw, Settings, AlertCircle, CheckCircle2, Power, Layers } from '@lucide/svelte';

	let { data }: PageProps = $props();

	let plugins = $state<PluginSummary[]>([]);
	$effect(() => {
		plugins = data.plugins;
	});

	let reloading = $state(false);
	let busyId = $state<string | null>(null);
	let message = $state<string | null>(null);
	let error = $state<string | null>(null);

	let selectedPlugin = $state<PluginSummary | null>(null);
	let configModalOpen = $state(false);

	const CAPABILITY_LABELS: Record<string, string> = {
		'metadata.movie': 'Movies',
		'metadata.series': 'TV Shows',
		'metadata.music': 'Music',
		'metadata.book': 'Books',
		'notification': 'Notifications',
		'api.router': 'API Extension',
		'scrobbler': 'Scrobbler'
	};

	function formatCapability(cap: string): string {
		return CAPABILITY_LABELS[cap] ?? cap;
	}

	function statusLabel(status: string): string {
		switch (status) {
			case 'active':
				return 'Active';
			case 'disabled':
				return 'Disabled';
			case 'config_required':
				return 'Config Required';
			case 'failed':
				return 'Failed';
			default:
				return status;
		}
	}

	async function reloadPlugins(): Promise<void> {
		reloading = true;
		message = null;
		error = null;
		try {
			plugins = await api.post<PluginSummary[]>('/plugins/reload');
			message = 'Plugins reloaded from disk.';
			await invalidateAll();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			reloading = false;
		}
	}

	async function togglePlugin(plugin: PluginSummary, enable: boolean): Promise<void> {
		busyId = plugin.id;
		message = null;
		error = null;
		try {
			const action = enable ? 'enable' : 'disable';
			const updated = await api.post<PluginSummary>(`/plugins/${plugin.id}/${action}`);
			plugins = plugins.map((p) => (p.id === updated.id ? updated : p));
			message = `Plugin “${plugin.name}” ${enable ? 'enabled' : 'disabled'}.`;
			await invalidateAll();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			busyId = null;
		}
	}

	function openConfig(plugin: PluginSummary): void {
		selectedPlugin = plugin;
		configModalOpen = true;
	}

	async function handleConfigSaved(_saved: PluginConfigResponse): Promise<void> {
		message = 'Configuration saved successfully.';
		try {
			plugins = await api.get<PluginSummary[]>('/plugins');
			await invalidateAll();
		} catch {
			// Ignore refresh error
		}
	}
</script>

<svelte:head>
	<title>Plugins · Admin · Hearth</title>
</svelte:head>

<section class="admin-header">
	<div class="header-text">
		<h2>Plugins</h2>
		<p class="muted">
			Manage metadata providers, external notification services, and client adapters.
		</p>
	</div>
	<button
		type="button"
		class="ghost-btn reload-btn"
		disabled={reloading}
		onclick={reloadPlugins}
		title="Rescan plugin directories from disk"
	>
		<RefreshCw size={15} class={reloading ? 'spin' : ''} />
		<span>{reloading ? 'Reloading…' : 'Reload from disk'}</span>
	</button>
</section>

{#if message}
	<p class="banner ok" role="status">
		<CheckCircle2 size={16} /> <span>{message}</span>
	</p>
{/if}
{#if error}
	<p class="banner err" role="alert">
		<AlertCircle size={16} /> <span>{error}</span>
	</p>
{/if}

{#if plugins.length === 0}
	<div class="empty card">
		<Layers size={36} class="muted" />
		<p class="muted">No plugins discovered in built-in or custom plugin directories.</p>
	</div>
{:else}
	<div class="plugins-grid">
		{#each plugins as plugin (plugin.id)}
			<article class="plugin-card card" class:is-disabled={plugin.status === 'disabled'}>
				<div class="card-head">
					<div class="title-wrap">
						<h3>{plugin.name}</h3>
						<div class="meta-line">
							<span class="version">v{plugin.version}</span>
							{#if plugin.author}
								<span class="muted">·</span>
								<span class="author">{plugin.author}</span>
							{/if}
						</div>
					</div>
					<div class="badges-wrap">
						{#if plugin.is_builtin}
							<span class="chip builtin">Built-in</span>
						{:else}
							<span class="chip custom">Custom</span>
						{/if}
						<span class="status-badge {plugin.status}">
							{statusLabel(plugin.status)}
						</span>
					</div>
				</div>

				<p class="desc">{plugin.description}</p>

				{#if plugin.status === 'failed' && plugin.error_message}
					<div class="error-box">
						<AlertCircle size={14} />
						<span>{plugin.error_message}</span>
					</div>
				{/if}

				{#if plugin.capabilities && plugin.capabilities.length > 0}
					<div class="caps-wrap">
						<span class="caps-label">Capabilities:</span>
						<ul class="caps-list">
							{#each plugin.capabilities as cap}
								<li class="cap-chip">{formatCapability(cap)}</li>
							{/each}
						</ul>
					</div>
				{/if}

				<div class="card-footer">
					<div class="left-actions">
						{#if plugin.status === 'active'}
							<button
								type="button"
								class="action-btn disable"
								disabled={busyId === plugin.id}
								onclick={() => togglePlugin(plugin, false)}
							>
								<Power size={14} />
								<span>{busyId === plugin.id ? 'Disabling…' : 'Disable'}</span>
							</button>
						{:else}
							<button
								type="button"
								class="action-btn enable"
								disabled={busyId === plugin.id}
								onclick={() => togglePlugin(plugin, true)}
							>
								<Power size={14} />
								<span>{busyId === plugin.id ? 'Enabling…' : 'Enable'}</span>
							</button>
						{/if}
					</div>

					<div class="right-actions">
						{#if plugin.has_config_schema}
							<button
								type="button"
								class="ghost-btn config-btn"
								onclick={() => openConfig(plugin)}
								title="Configure {plugin.name}"
							>
								<Settings size={14} />
								<span>Settings</span>
							</button>
						{/if}
					</div>
				</div>
			</article>
		{/each}
	</div>
{/if}

<PluginConfigModal
	plugin={selectedPlugin}
	open={configModalOpen}
	onclose={() => (configModalOpen = false)}
	onsaved={handleConfigSaved}
/>

<style>
	.admin-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		flex-wrap: wrap;
		gap: 1rem;
		margin-bottom: 1.25rem;
	}

	.header-text h2 {
		margin: 0;
		font-size: 1.4rem;
	}

	.header-text p {
		margin: 0.25rem 0 0;
		font-size: 0.9rem;
	}

	.reload-btn {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		font-size: 0.85rem;
		padding: 0.45rem 0.85rem;
	}

	:global(.spin) {
		animation: spin 1s linear infinite;
	}

	@keyframes spin {
		from {
			transform: rotate(0deg);
		}
		to {
			transform: rotate(360deg);
		}
	}

	.banner {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.65rem 1rem;
		border-radius: var(--radius);
		font-size: 0.9rem;
		margin-bottom: 1.25rem;
	}

	.banner.ok {
		background: rgba(46, 160, 67, 0.15);
		border: 1px solid #2ea043;
		color: #3fb950;
	}

	.banner.err {
		background: rgba(255, 107, 107, 0.15);
		border: 1px solid var(--danger);
		color: var(--danger);
	}

	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 1.25rem;
	}

	.empty {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 0.75rem;
		padding: 3rem 1rem;
		text-align: center;
	}

	.plugins-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(21rem, 1fr));
		gap: 1.25rem;
	}

	.plugin-card {
		display: flex;
		flex-direction: column;
		gap: 0.85rem;
		transition: border-color 0.15s ease;
	}

	.plugin-card.is-disabled {
		opacity: 0.75;
	}

	.plugin-card:hover {
		border-color: var(--border);
	}

	.card-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 0.75rem;
	}

	.title-wrap h3 {
		font-size: 1.15rem;
		margin: 0;
	}

	.meta-line {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.8rem;
		color: var(--muted);
		margin-top: 0.2rem;
	}

	.badges-wrap {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		flex-shrink: 0;
	}

	.chip {
		font-size: 0.7rem;
		padding: 0.1rem 0.45rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: var(--surface-2);
		color: var(--muted);
		text-transform: uppercase;
		letter-spacing: 0.03em;
		font-weight: 500;
	}

	.status-badge {
		font-size: 0.72rem;
		padding: 0.15rem 0.55rem;
		border-radius: 999px;
		font-weight: 600;
		text-transform: capitalize;
		border: 1px solid transparent;
	}

	.status-badge.active {
		background: rgba(46, 160, 67, 0.15);
		border-color: #2ea043;
		color: #3fb950;
	}

	.status-badge.disabled {
		background: var(--surface-2);
		border-color: var(--border);
		color: var(--muted);
	}

	.status-badge.config_required {
		background: rgba(230, 162, 60, 0.15);
		border-color: #e6a23c;
		color: #e6a23c;
	}

	.status-badge.failed {
		background: rgba(255, 107, 107, 0.15);
		border-color: var(--danger);
		color: var(--danger);
	}

	.desc {
		font-size: 0.875rem;
		color: var(--text);
		margin: 0;
		line-height: 1.45;
		flex: 1;
	}

	.error-box {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.8rem;
		color: var(--danger);
		background: rgba(255, 107, 107, 0.1);
		border: 1px solid rgba(255, 107, 107, 0.25);
		padding: 0.4rem 0.65rem;
		border-radius: 6px;
	}

	.caps-wrap {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.4rem;
		font-size: 0.8rem;
	}

	.caps-label {
		color: var(--muted);
		font-size: 0.78rem;
	}

	.caps-list {
		display: flex;
		flex-wrap: wrap;
		gap: 0.35rem;
		list-style: none;
		padding: 0;
		margin: 0;
	}

	.cap-chip {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0.1rem 0.4rem;
		font-size: 0.75rem;
		color: var(--muted);
	}

	.card-footer {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		padding-top: 0.75rem;
		border-top: 1px solid var(--border);
		margin-top: auto;
	}

	.left-actions,
	.right-actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.action-btn {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		padding: 0.35rem 0.75rem;
		font-size: 0.82rem;
		border-radius: var(--radius);
		cursor: pointer;
		font-weight: 500;
		border: 1px solid transparent;
		transition: all 0.15s ease;
	}

	.action-btn.enable {
		background: rgba(46, 160, 67, 0.15);
		border-color: #2ea043;
		color: #3fb950;
	}

	.action-btn.enable:hover:not(:disabled) {
		background: #2ea043;
		color: #fff;
	}

	.action-btn.disable {
		background: var(--surface-2);
		border-color: var(--border);
		color: var(--muted);
	}

	.action-btn.disable:hover:not(:disabled) {
		border-color: var(--danger);
		color: var(--danger);
	}

	.action-btn:disabled,
	.ghost-btn:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.ghost-btn {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.35rem 0.75rem;
		cursor: pointer;
		color: var(--text);
		font-size: 0.82rem;
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
	}

	.ghost-btn:hover:not(:disabled) {
		background: var(--surface);
		border-color: var(--text);
	}

	.muted {
		color: var(--muted);
	}
</style>
