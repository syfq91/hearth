<script lang="ts">
	import { api } from '../api/client';
	import type { PluginSummary, PluginConfigResponse } from '../api/types';
	import { X, Eye, EyeOff, Check, AlertCircle } from '@lucide/svelte';

	interface Props {
		plugin: PluginSummary | null;
		open: boolean;
		onclose: () => void;
		onsaved?: (updated: PluginConfigResponse) => void;
	}

	let { plugin, open = false, onclose, onsaved }: Props = $props();

	let dialogEl = $state<HTMLDialogElement | null>(null);
	let loading = $state(false);
	let saving = $state(false);
	let error = $state<string | null>(null);
	let successMsg = $state<string | null>(null);

	let currentConfig = $state<Record<string, any>>({});
	let formValues = $state<Record<string, any>>({});
	let newSecrets = $state<Record<string, string>>({});
	let clearedSecrets = $state<Record<string, boolean>>({});
	let showSecrets = $state<Record<string, boolean>>({});
	let schema = $state<{
		title?: string;
		description?: string;
		type?: string;
		properties?: Record<string, {
			title?: string;
			description?: string;
			type?: string;
			secret?: boolean;
			default?: unknown;
			enum?: unknown[];
			minimum?: number;
			maximum?: number;
		}>;
		required?: string[];
	} | null>(null);

	$effect(() => {
		if (open && plugin) {
			dialogEl?.showModal();
			void loadConfig(plugin.id);
		} else {
			dialogEl?.close();
		}
	});

	async function loadConfig(pluginId: string): Promise<void> {
		loading = true;
		error = null;
		successMsg = null;
		formValues = {};
		newSecrets = {};
		clearedSecrets = {};
		showSecrets = {};
		try {
			const res = await api.get<PluginConfigResponse>(`/plugins/${pluginId}/config`);
			currentConfig = (res.config as Record<string, unknown>) || {};
			schema = (res.schema as typeof schema) || null;

			const props = schema?.properties || {};
			const initial: Record<string, unknown> = {};
			for (const [key, prop] of Object.entries(props)) {
				if (prop.secret) {
					newSecrets[key] = '';
					clearedSecrets[key] = false;
					showSecrets[key] = false;
				} else {
					initial[key] = currentConfig[key] ?? prop.default ?? (prop.type === 'boolean' ? false : '');
				}
			}
			formValues = initial;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			loading = false;
		}
	}

	async function handleSave(e: SubmitEvent): Promise<void> {
		e.preventDefault();
		if (!plugin) return;

		saving = true;
		error = null;
		successMsg = null;

		try {
			const payload: Record<string, unknown> = {};
			const props = schema?.properties || {};

			for (const [key, prop] of Object.entries(props)) {
				if (prop.secret) {
					if (clearedSecrets[key]) {
						payload[key] = '';
					} else if (newSecrets[key]?.trim()) {
						payload[key] = newSecrets[key].trim();
					} else if (currentConfig[key]) {
						payload[key] = currentConfig[key];
					}
				} else {
					if (prop.type === 'integer' || prop.type === 'number') {
						const num = Number(formValues[key]);
						payload[key] = isNaN(num) ? prop.default : num;
					} else {
						payload[key] = formValues[key];
					}
				}
			}

			const updated = await api.put<PluginConfigResponse>(`/plugins/${plugin.id}/config`, {
				body: { config: payload }
			});

			successMsg = 'Configuration saved.';
			onsaved?.(updated);
			setTimeout(() => {
				if (open) onclose();
			}, 500);
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			saving = false;
		}
	}
</script>

<dialog
	bind:this={dialogEl}
	class="config-dialog"
	oncancel={(e) => {
		e.preventDefault();
		onclose();
	}}
	onclick={(e) => {
		if (e.target === dialogEl) onclose();
	}}
>
	<div class="modal-content">
		<header class="modal-header">
			<div>
				<h3>{plugin?.name ?? 'Plugin'} Settings</h3>
				{#if plugin?.description}
					<p class="plugin-desc">{plugin.description}</p>
				{/if}
			</div>
			<button type="button" class="close-btn" aria-label="Close" onclick={onclose}>
				<X size={18} />
			</button>
		</header>

		<div class="modal-body">
			{#if loading}
				<div class="status-msg">Loading configuration…</div>
			{:else if error && !schema}
				<div class="status-msg err">
					<AlertCircle size={20} />
					<span>{error}</span>
				</div>
			{:else if !schema?.properties || Object.keys(schema.properties).length === 0}
				<div class="status-msg">This plugin has no configurable options.</div>
			{:else}
				<form id="config-form" onsubmit={handleSave}>
					<div class="fields-list">
						{#each Object.entries(schema.properties) as [key, prop] (key)}
							<div class="field-group">
								{#if prop.type === 'boolean'}
									<label class="check-label">
										<input type="checkbox" bind:checked={formValues[key]} />
										<span class="field-title">
											{prop.title ?? key}
											{#if schema.required?.includes(key)}
												<span class="required" aria-hidden="true">*</span>
											{/if}
										</span>
									</label>
									{#if prop.description}
										<p class="field-desc">{prop.description}</p>
									{/if}
								{:else if prop.secret}
									<div class="field-head">
										<span class="field-title">
											{prop.title ?? key}
											{#if schema.required?.includes(key)}
												<span class="required" aria-hidden="true">*</span>
											{/if}
										</span>
										{#if currentConfig[key]}
											<span class="badge configured">
												Configured ({currentConfig[key]})
											</span>
										{/if}
									</div>

									{#if prop.description}
										<p class="field-desc">{prop.description}</p>
									{/if}

									<div class="secret-row">
										<div class="input-with-action">
											<input
												type={showSecrets[key] ? 'text' : 'password'}
												bind:value={newSecrets[key]}
												placeholder={currentConfig[key]
													? 'Leave blank to keep existing secret'
													: 'Enter secret value'}
												disabled={clearedSecrets[key]}
												autocomplete="off"
												spellcheck="false"
											/>
											<button
												type="button"
												class="toggle-show"
												aria-label={showSecrets[key] ? 'Hide secret' : 'Show secret'}
												onclick={() => (showSecrets[key] = !showSecrets[key])}
												disabled={clearedSecrets[key]}
											>
												{#if showSecrets[key]}
													<EyeOff size={16} />
												{:else}
													<Eye size={16} />
												{/if}
											</button>
										</div>

										{#if currentConfig[key]}
											<label class="clear-check">
												<input type="checkbox" bind:checked={clearedSecrets[key]} />
												<span>Remove</span>
											</label>
										{/if}
									</div>
								{:else if prop.enum && Array.isArray(prop.enum)}
									<label>
										<span class="field-title">
											{prop.title ?? key}
											{#if schema.required?.includes(key)}
												<span class="required" aria-hidden="true">*</span>
											{/if}
										</span>
										{#if prop.description}
											<p class="field-desc">{prop.description}</p>
										{/if}
										<select bind:value={formValues[key]}>
											{#each prop.enum as option}
												<option value={option}>{option}</option>
											{/each}
										</select>
									</label>
								{:else if prop.type === 'integer' || prop.type === 'number'}
									<label>
										<span class="field-title">
											{prop.title ?? key}
											{#if schema.required?.includes(key)}
												<span class="required" aria-hidden="true">*</span>
											{/if}
										</span>
										{#if prop.description}
											<p class="field-desc">{prop.description}</p>
										{/if}
										<input
											type="number"
											bind:value={formValues[key]}
											min={prop.minimum}
											max={prop.maximum}
											placeholder={prop.default !== undefined ? String(prop.default) : ''}
										/>
									</label>
								{:else}
									<label>
										<span class="field-title">
											{prop.title ?? key}
											{#if schema.required?.includes(key)}
												<span class="required" aria-hidden="true">*</span>
											{/if}
										</span>
										{#if prop.description}
											<p class="field-desc">{prop.description}</p>
										{/if}
										<input
											type="text"
											bind:value={formValues[key]}
											placeholder={prop.default !== undefined ? String(prop.default) : ''}
										/>
									</label>
								{/if}
							</div>
						{/each}
					</div>
				</form>
			{/if}
		</div>

		<footer class="modal-footer">
			{#if error && schema}
				<p class="err-inline" role="alert">{error}</p>
			{:else if successMsg}
				<p class="ok-inline" role="status">
					<Check size={16} /> {successMsg}
				</p>
			{:else}
				<div></div>
			{/if}

			<div class="footer-actions">
				<button type="button" class="ghost-btn" onclick={onclose} disabled={saving}>
					Cancel
				</button>
				{#if schema?.properties && Object.keys(schema.properties).length > 0}
					<button
						type="submit"
						form="config-form"
						class="primary-btn"
						disabled={saving || loading}
					>
						{saving ? 'Saving…' : 'Save Changes'}
					</button>
				{/if}
			</div>
		</footer>
	</div>
</dialog>

<style>
	.config-dialog {
		background: var(--surface);
		color: var(--text);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0;
		max-width: 36rem;
		width: 92vw;
		box-shadow: 0 16px 40px rgba(0, 0, 0, 0.55);
	}

	.config-dialog::backdrop {
		background: rgba(0, 0, 0, 0.7);
		backdrop-filter: blur(2px);
	}

	.modal-content {
		display: flex;
		flex-direction: column;
		max-height: 85vh;
	}

	.modal-header {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		padding: 1rem 1.25rem;
		border-bottom: 1px solid var(--border);
		gap: 1rem;
	}

	.modal-header h3 {
		font-size: 1.15rem;
		margin: 0;
	}

	.plugin-desc {
		font-size: 0.85rem;
		color: var(--muted);
		margin: 0.25rem 0 0;
	}

	.close-btn {
		background: transparent;
		border: 0;
		color: var(--muted);
		cursor: pointer;
		padding: 0.25rem;
		border-radius: 4px;
		line-height: 1;
	}

	.close-btn:hover {
		color: var(--text);
		background: var(--surface-2);
	}

	.modal-body {
		flex: 1;
		overflow-y: auto;
		padding: 1.25rem;
	}

	.status-msg {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0.5rem;
		padding: 2.5rem 1rem;
		color: var(--muted);
		font-size: 0.95rem;
	}

	.status-msg.err {
		color: var(--danger);
	}

	.fields-list {
		display: flex;
		flex-direction: column;
		gap: 1.25rem;
	}

	.field-group {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
	}

	.field-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem;
	}

	.field-title {
		font-weight: 500;
		font-size: 0.95rem;
	}

	.field-desc {
		font-size: 0.82rem;
		color: var(--muted);
		margin: 0 0 0.25rem;
	}

	.required {
		color: var(--danger);
		margin-left: 0.15rem;
	}

	.badge {
		font-size: 0.72rem;
		padding: 0.1rem 0.5rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: var(--surface-2);
		color: var(--muted);
	}

	.badge.configured {
		border-color: #2ea043;
		color: #3fb950;
	}

	.check-label {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		cursor: pointer;
		user-select: none;
	}

	.check-label input[type='checkbox'] {
		width: 1.1rem;
		height: 1.1rem;
		accent-color: var(--accent);
		cursor: pointer;
	}

	input[type='text'],
	input[type='password'],
	input[type='number'],
	select {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.5rem 0.75rem;
		color: var(--text);
		font-size: 0.9rem;
		width: 100%;
	}

	input:focus,
	select:focus {
		border-color: var(--accent);
		outline: none;
	}

	.secret-row {
		display: flex;
		align-items: center;
		gap: 0.75rem;
	}

	.input-with-action {
		position: relative;
		flex: 1;
		display: flex;
		align-items: center;
	}

	.input-with-action input {
		padding-right: 2.5rem;
	}

	.toggle-show {
		position: absolute;
		right: 0.5rem;
		background: transparent;
		border: 0;
		color: var(--muted);
		cursor: pointer;
		padding: 0.25rem;
		border-radius: 4px;
		display: flex;
		align-items: center;
	}

	.toggle-show:hover:not(:disabled) {
		color: var(--text);
	}

	.toggle-show:disabled {
		opacity: 0.4;
		cursor: default;
	}

	.clear-check {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.82rem;
		color: var(--danger);
		cursor: pointer;
		white-space: nowrap;
		user-select: none;
	}

	.clear-check input[type='checkbox'] {
		accent-color: var(--danger);
	}

	.modal-footer {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.85rem 1.25rem;
		border-top: 1px solid var(--border);
		background: var(--surface);
		gap: 1rem;
	}

	.footer-actions {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-left: auto;
	}

	.ghost-btn {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.45rem 1rem;
		cursor: pointer;
		color: var(--text);
		font-size: 0.9rem;
	}

	.ghost-btn:hover:not(:disabled) {
		background: var(--surface);
	}

	.primary-btn {
		background: var(--accent);
		color: var(--accent-contrast);
		border: 0;
		border-radius: var(--radius);
		padding: 0.45rem 1.2rem;
		font-weight: 600;
		cursor: pointer;
		font-size: 0.9rem;
	}

	.primary-btn:hover:not(:disabled) {
		opacity: 0.95;
	}

	.primary-btn:disabled,
	.ghost-btn:disabled {
		opacity: 0.5;
		cursor: default;
	}

	.ok-inline {
		display: inline-flex;
		align-items: center;
		gap: 0.35rem;
		color: #3fb950;
		font-size: 0.85rem;
		margin: 0;
	}

	.err-inline {
		color: var(--danger);
		font-size: 0.85rem;
		margin: 0;
	}
</style>
