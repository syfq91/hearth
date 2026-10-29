<script lang="ts">
	import { api } from '../../lib/api/client';
	import { session } from '../../lib/auth.svelte';

	let user = $derived(session.user);

	/* ---- password ---- */
	let newPassword = $state('');
	let confirmPassword = $state('');
	let passwordBusy = $state(false);
	let passwordMessage = $state<string | null>(null);
	let passwordError = $state<string | null>(null);

	async function changePassword(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		if (!user) return;
		passwordBusy = true;
		passwordMessage = null;
		passwordError = null;
		try {
			await api.post(`/users/${user.id}/password`, {
				body: { password: newPassword }
			});
			passwordMessage = 'Password updated. Existing sessions stay signed in.';
			newPassword = '';
			confirmPassword = '';
		} catch (e) {
			passwordError = e instanceof Error ? e.message : String(e);
		} finally {
			passwordBusy = false;
		}
	}

	/* ---- ListenBrainz ---- */
	let lbToken = $state('');
	let lbBusy = $state(false);
	let lbMessage = $state<string | null>(null);
	let lbError = $state<string | null>(null);

	async function saveListenBrainz(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		lbBusy = true;
		lbMessage = null;
		lbError = null;
		try {
			await api.put('/users/me/listenbrainz', { body: { token: lbToken } });
			lbMessage = 'ListenBrainz token saved.';
			lbToken = '';
		} catch (e) {
			lbError = e instanceof Error ? e.message : String(e);
		} finally {
			lbBusy = false;
		}
	}

	async function clearListenBrainz(): Promise<void> {
		lbBusy = true;
		lbError = null;
		try {
			await api.del('/users/me/listenbrainz');
			lbMessage = 'ListenBrainz disconnected.';
		} catch (e) {
			lbError = e instanceof Error ? e.message : String(e);
		} finally {
			lbBusy = false;
		}
	}
</script>

<svelte:head>
	<title>Profile · Hearth</title>
</svelte:head>

<h1>Profile</h1>

{#if user}
	<section class="card" aria-labelledby="account-heading">
		<h2 id="account-heading">Account</h2>
		<dl>
			<div>
				<dt>Username</dt>
				<dd>{user.username}</dd>
			</div>
			<div>
				<dt>Role</dt>
				<dd>{user.role}</dd>
			</div>
			<div>
				<dt>Member since</dt>
				<dd>{new Date(user.created_at).toLocaleDateString()}</dd>
			</div>
		</dl>
	</section>

	<section class="card" aria-labelledby="password-heading">
		<h2 id="password-heading">Change password</h2>
		<form class="stack" onsubmit={changePassword}>
			<label>
				<span>New password</span>
				<input type="password" bind:value={newPassword} required minlength={8} autocomplete="new-password" />
			</label>
			<label>
				<span>Confirm new password</span>
				<input type="password" bind:value={confirmPassword} required minlength={8} autocomplete="new-password" />
			</label>
			<button type="submit" disabled={passwordBusy || newPassword !== confirmPassword}>
				{passwordBusy ? 'Saving…' : 'Update password'}
			</button>
			{#if newPassword && confirmPassword && newPassword !== confirmPassword}
				<p class="hint" role="status">Passwords do not match yet.</p>
			{/if}
			{#if passwordMessage}
				<p class="ok" role="status">{passwordMessage}</p>
			{/if}
			{#if passwordError}
				<p class="err" role="alert">{passwordError}</p>
			{/if}
		</form>
	</section>

	<section class="card" aria-labelledby="lb-heading">
		<h2 id="lb-heading">ListenBrainz</h2>
		<p class="hint">Scrobble plays to ListenBrainz with a personal access token.</p>
		<form class="row" onsubmit={saveListenBrainz}>
			<label class="grow">
				<span>Token</span>
				<input type="password" bind:value={lbToken} placeholder="lb_…" autocomplete="off" />
			</label>
			<button type="submit" disabled={lbBusy || lbToken.length === 0}>Save</button>
			<button type="button" class="ghost" onclick={clearListenBrainz} disabled={lbBusy}>
				Disconnect
			</button>
		</form>
		{#if lbMessage}
			<p class="ok" role="status">{lbMessage}</p>
		{/if}
		{#if lbError}
			<p class="err" role="alert">{lbError}</p>
		{/if}
	</section>
{:else}
	<p class="hint">Sign in to manage your profile.</p>
{/if}

<style>
	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 1.25rem;
		margin-bottom: 1.25rem;
		max-width: 40rem;
	}

	.card h2 {
		font-size: 1.05rem;
		margin-bottom: 0.75rem;
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

	.stack {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.row {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 0.75rem;
	}

	.grow {
		flex: 1;
		min-width: 14rem;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		font-size: 0.85rem;
		color: var(--muted);
	}

	input {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.5rem 0.6rem;
		color: var(--text);
	}

	button[type='submit'] {
		background: var(--accent);
		color: var(--accent-contrast);
		font-weight: 600;
		border: 0;
		border-radius: var(--radius);
		padding: 0.55rem 1.2rem;
		cursor: pointer;
		align-self: flex-start;
	}

	button[type='submit']:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.ghost {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.55rem 1rem;
		cursor: pointer;
	}

	.hint {
		color: var(--muted);
		font-size: 0.9rem;
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
