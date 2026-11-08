<script lang="ts">
	import { untrack } from 'svelte';
	import { api } from '../../../lib/api/client';
	import type { MetadataProviderStatus, MetadataSettingsResponse } from '../../../lib/api/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let settings = $state<MetadataSettingsResponse>(untrack(() => data.settings));
	let values = $state<Record<string, string>>({});
	let remove = $state<Record<string, boolean>>({});
	let saving = $state(false);
	let message = $state<string | null>(null);
	let error = $state<string | null>(null);

	const keyProviders = $derived(
		settings.providers.filter((provider) => provider.requires_api_key)
	);
	const otherProviders = $derived(
		settings.providers.filter((provider) => !provider.requires_api_key)
	);

	function sourceLabel(provider: MetadataProviderStatus): string {
		if (!provider.requires_api_key) return 'No API key required';
		if (provider.source === 'database') return provider.configured ? 'Saved in admin' : 'Cleared';
		if (provider.source === 'environment') return 'From environment';
		return 'Not configured';
	}

	async function save(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		saving = true;
		message = null;
		error = null;

		const body: Record<string, string> = {};
		for (const provider of keyProviders) {
			if (!provider.api_key_field) continue;
			if (remove[provider.name]) {
				body[provider.api_key_field] = '';
			} else if (values[provider.name]?.trim()) {
				body[provider.api_key_field] = values[provider.name].trim();
			}
		}

		if (Object.keys(body).length === 0) {
			message = 'No changes to save.';
			saving = false;
			return;
		}

		try {
			settings = await api.put<MetadataSettingsResponse>('/admin/metadata/settings', { body });
			values = {};
			remove = {};
			message = 'Metadata provider settings saved.';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			saving = false;
		}
	}
</script>

<svelte:head>
	<title>Metadata · Admin · Hearth</title>
</svelte:head>

<section class="card" aria-labelledby="metadata-heading">
	<h2 id="metadata-heading">Online metadata services</h2>
	<p class="intro">
		Add API keys for the services Hearth uses to match movies, shows, music, and books. Keys
		are stored on the server and are never shown again after saving; only the last four
		characters are displayed.
	</p>

	<form onsubmit={save}>
		<ul class="providers">
			{#each keyProviders as provider (provider.name)}
				<li class="provider">
					<div class="provider-head">
						<div>
							<h3>{provider.label}</h3>
							<p class="muted">{provider.description}</p>
						</div>
						<span class:configured={provider.configured} class="chip">
							{sourceLabel(provider)}
						</span>
					</div>

					<div class="key-row">
						<label>
							<span>{provider.label} API key</span>
							<input
								type="password"
								name={provider.api_key_field ?? provider.name}
								placeholder={provider.configured
									? `Configured (${provider.key_hint})`
									: 'Paste API key'}
								autocomplete="off"
								spellcheck="false"
								bind:value={values[provider.name]}
								disabled={remove[provider.name]}
							/>
						</label>
						{#if provider.configured}
							<label class="check">
								<input type="checkbox" bind:checked={remove[provider.name]} />
								<span>Remove key</span>
							</label>
						{/if}
					</div>

					{#if provider.docs_url}
						<p class="docs">
							<a href={provider.docs_url} target="_blank" rel="noreferrer">
								Get an API key
							</a>
						</p>
					{/if}
				</li>
			{/each}
		</ul>

		<button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save API keys'}</button>
	</form>

	{#if message}
		<p class="ok" role="status">{message}</p>
	{/if}
	{#if error}
		<p class="err" role="alert">{error}</p>
	{/if}
</section>

<section class="card" aria-labelledby="no-key-heading">
	<h2 id="no-key-heading">Services without API keys</h2>
	<ul class="providers">
		{#each otherProviders as provider (provider.name)}
			<li class="provider">
				<div class="provider-head">
					<div>
						<h3>{provider.label}</h3>
						<p class="muted">{provider.description}</p>
					</div>
					<span class="chip">{provider.enabled ? 'Enabled' : 'Disabled'}</span>
				</div>
				{#if provider.docs_url}
					<p class="docs">
						<a href={provider.docs_url} target="_blank" rel="noreferrer">Provider API docs</a>
					</p>
				{/if}
			</li>
		{/each}
	</ul>
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

	.intro {
		color: var(--muted);
		font-size: 0.9rem;
		margin: 0 0 1rem;
	}

	.providers {
		list-style: none;
		padding: 0;
		margin: 0 0 1rem;
		display: grid;
		gap: 0.85rem;
	}

	.provider {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.85rem 1rem;
	}

	.provider-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
	}

	h3 {
		font-size: 0.95rem;
		margin: 0 0 0.2rem;
	}

	.chip {
		font-size: 0.72rem;
		color: var(--muted);
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0.15rem 0.6rem;
		white-space: nowrap;
	}

	.chip.configured {
		color: var(--accent);
		border-color: var(--accent);
	}

	.key-row {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 1rem;
		margin-top: 0.75rem;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.85rem;
		color: var(--muted);
		flex: 1 1 18rem;
	}

	label.check {
		flex: 0 0 auto;
		flex-direction: row;
		align-items: center;
		gap: 0.4rem;
		padding-bottom: 0.55rem;
	}

	input[type='password'] {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.45rem 0.6rem;
		color: var(--text);
		font: inherit;
	}

	input[type='password']:disabled {
		opacity: 0.55;
	}

	button[type='submit'] {
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		border: 0;
		border-radius: var(--radius);
		padding: 0.5rem 1.1rem;
		cursor: pointer;
	}

	button[type='submit']:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.muted {
		color: var(--muted);
		font-size: 0.85rem;
		margin: 0;
	}

	.docs {
		margin: 0.6rem 0 0;
		font-size: 0.85rem;
	}

	.ok {
		color: var(--accent);
		font-size: 0.9rem;
		margin: 0.75rem 0 0;
	}

	.err {
		color: var(--danger);
		font-size: 0.9rem;
		margin: 0.75rem 0 0;
	}
</style>
