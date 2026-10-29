<script lang="ts">
	import { login, session } from '../../lib/auth.svelte';
	import { ApiError } from '../../lib/api/client';
	import { goto } from '$app/navigation';

	let username = $state('');
	let password = $state('');
	let submitting = $state(false);
	let error = $state<string | null>(null);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		error = null;
		if (!username || !password) {
			error = 'Enter your username and password.';
			return;
		}
		submitting = true;
		try {
			await login(username, password);
			await goto('/');
		} catch (err) {
			error =
				err instanceof ApiError
					? err.message
					: 'Could not sign in. Is the server reachable?';
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Sign in · Hearth</title>
</svelte:head>

<div class="auth-wrap">
	<section class="card" aria-labelledby="login-title">
		<h1 id="login-title">Sign in</h1>
		{#if session.status === 'authenticated'}
			<p>You are already signed in.</p>
		{:else}
			<form onsubmit={submit}>
				<label>
					Username
					<input
						name="username"
						autocomplete="username"
						bind:value={username}
						required
					/>
				</label>
				<label>
					Password
					<input
						name="password"
						type="password"
						autocomplete="current-password"
						bind:value={password}
						required
					/>
				</label>
				{#if error}
					<p class="error" role="alert">{error}</p>
				{/if}
				<button type="submit" class="primary" disabled={submitting}>
					{submitting ? 'Signing in…' : 'Sign in'}
				</button>
			</form>
			<p class="muted">
				First time here? <a href="/setup">Set up the admin account</a>.
			</p>
		{/if}
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
