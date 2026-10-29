<script lang="ts">
	import { setup } from '../../lib/auth.svelte';
	import { ApiError } from '../../lib/api/client';
	import { goto } from '$app/navigation';

	let username = $state('');
	let password = $state('');
	let confirm = $state('');
	let submitting = $state(false);
	let error = $state<string | null>(null);

	const MIN_PASSWORD_LENGTH = 8;

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		error = null;
		if (password.length < MIN_PASSWORD_LENGTH) {
			error = `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
			return;
		}
		if (password !== confirm) {
			error = 'Passwords do not match.';
			return;
		}
		submitting = true;
		try {
			await setup(username, password);
			await goto('/');
		} catch (err) {
			if (err instanceof ApiError && err.code === 'setup_complete') {
				error = 'Hearth is already set up — sign in instead.';
			} else {
				error =
					err instanceof ApiError
						? err.message
						: 'Setup failed. Is the server reachable?';
			}
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>First-run setup · Hearth</title>
</svelte:head>

<div class="auth-wrap">
	<section class="card" aria-labelledby="setup-title">
		<h1 id="setup-title">Set up Hearth</h1>
		<p class="muted">Create the first administrator account.</p>
		<form onsubmit={submit}>
			<label>
				Username
				<input name="username" autocomplete="username" bind:value={username} required />
			</label>
			<label>
				Password <span class="hint">({MIN_PASSWORD_LENGTH}+ characters)</span>
				<input
					name="password"
					type="password"
					autocomplete="new-password"
					bind:value={password}
					required
				/>
			</label>
			<label>
				Confirm password
				<input
					name="confirm"
					type="password"
					autocomplete="new-password"
					bind:value={confirm}
					required
				/>
			</label>
			{#if error}
				<p class="error" role="alert">{error}</p>
			{/if}
			<button type="submit" class="primary" disabled={submitting}>
				{submitting ? 'Creating…' : 'Create admin account'}
			</button>
		</form>
		<p class="muted">Already have an account? <a href="/login">Sign in</a>.</p>
	</section>
</div>

<style>
	.auth-wrap {
		display: flex;
		justify-content: center;
		padding-top: 10vh;
	}

	.card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 1.5rem;
		width: min(24rem, 100%);
	}

	form {
		display: flex;
		flex-direction: column;
		gap: 0.9rem;
	}

	label {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		font-size: 0.9rem;
		color: var(--muted);
	}

	.hint {
		color: var(--muted);
		opacity: 0.8;
	}

	input {
		background: var(--bg);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.55rem 0.7rem;
		color: var(--text);
	}

	.primary {
		background: var(--accent);
		color: var(--accent-contrast);
		border: 0;
		border-radius: var(--radius);
		padding: 0.6rem 1rem;
		font-weight: 600;
		cursor: pointer;
	}

	.primary:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.error {
		color: var(--danger);
		margin: 0;
	}

	.muted {
		color: var(--muted);
		font-size: 0.9rem;
	}
</style>
