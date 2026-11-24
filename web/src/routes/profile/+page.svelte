<script lang="ts">
	import { api } from '../../lib/api/client';
	import { reloadUser, session } from '../../lib/auth.svelte';
	import { COMMON_LANGUAGES } from '../../lib/languages';
	import { loadReplayGain, playerUi, setReplayGain } from '../../lib/player/ui.svelte';

	let user = $derived(session.user);

	// The audio dock normally loads this, but the profile page can be reached
	// directly (and the dock is hidden on video pages), so load it here too.
	loadReplayGain();

	/* ---- Playback preferences ---- */
	let preferredAudio = $state('');
	let preferredSubtitle = $state('');
	let subtitleMode = $state<'always' | 'never' | 'smart'>('smart');
	let autoPlayNext = $state<'yes' | 'no' | 'prompt'>('prompt');
	let prefBusy = $state(false);
	let prefMessage = $state<string | null>(null);
	let prefError = $state<string | null>(null);

	$effect(() => {
		if (user) {
			preferredAudio = user.preferred_audio_language ?? '';
			preferredSubtitle = user.preferred_subtitle_language ?? '';
			subtitleMode = (user.subtitle_mode as 'always' | 'never' | 'smart') ?? 'smart';
			autoPlayNext = (user.auto_play_next as 'yes' | 'no' | 'prompt') ?? 'prompt';
		}
	});

	async function savePreferences(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		prefBusy = true;
		prefMessage = null;
		prefError = null;
		try {
			await api.patch('/users/me/preferences', {
				body: {
					preferred_audio_language: preferredAudio || null,
					preferred_subtitle_language: preferredSubtitle || null,
					subtitle_mode: subtitleMode,
					auto_play_next: autoPlayNext
				}
			});
			await reloadUser();
			prefMessage = 'Preferences saved.';
		} catch (e) {
			prefError = e instanceof Error ? e.message : String(e);
		} finally {
			prefBusy = false;
		}
	}

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

	<section class="card" aria-labelledby="pref-heading">
		<h2 id="pref-heading">Playback preferences</h2>
		<form class="stack" onsubmit={savePreferences}>
			<label>
				<span>Preferred audio language</span>
				<select bind:value={preferredAudio}>
					<option value="">Default / Original</option>
					{#each COMMON_LANGUAGES as lang}
						<option value={lang.code}>{lang.label} ({lang.code})</option>
					{/each}
				</select>
			</label>
			<label>
				<span>Preferred subtitle language</span>
				<select bind:value={preferredSubtitle}>
					<option value="">None / Off</option>
					{#each COMMON_LANGUAGES as lang}
						<option value={lang.code}>{lang.label} ({lang.code})</option>
					{/each}
				</select>
			</label>
			<label>
				<span>Subtitle display</span>
				<select bind:value={subtitleMode}>
					<option value="smart">Smart (when audio is foreign)</option>
					<option value="always">Always show</option>
					<option value="never">Never show</option>
				</select>
			</label>
			<label>
				<span>Auto-play next episode</span>
				<select bind:value={autoPlayNext}>
					<option value="prompt">Prompt (ask when episode ends)</option>
					<option value="yes">Yes (play next automatically)</option>
					<option value="no">No (do not play next)</option>
				</select>
			</label>
			<button type="submit" disabled={prefBusy}>
				{prefBusy ? 'Saving…' : 'Save preferences'}
			</button>
			{#if prefMessage}
				<p class="ok" role="status">{prefMessage}</p>
			{/if}
			{#if prefError}
				<p class="err" role="alert">{prefError}</p>
			{/if}
		</form>
	</section>

	<section class="card" aria-labelledby="music-heading">
		<h2 id="music-heading">Music playback</h2>
		<label class="toggle">
			<input
				type="checkbox"
				aria-label="ReplayGain volume leveling"
				checked={playerUi.replayGain}
				onchange={(event) => setReplayGain(event.currentTarget.checked)}
			/>
			<span>
				<strong>ReplayGain volume leveling</strong>
				<small>
					Normalize track loudness using ReplayGain tags. Stored in this browser; on mobile it
					can stop music from playing in the background.
				</small>
			</span>
		</label>
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

	.toggle {
		flex-direction: row;
		align-items: flex-start;
		gap: 0.65rem;
		color: var(--text);
		cursor: pointer;
	}

	.toggle input[type='checkbox'] {
		flex: 0 0 auto;
		width: 1rem;
		height: 1rem;
		margin: 0.1rem 0 0;
		padding: 0;
		border: 0;
		background: transparent;
		accent-color: var(--accent);
		cursor: pointer;
	}

	.toggle span {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
	}

	.toggle small {
		color: var(--muted);
		font-size: 0.85rem;
		line-height: 1.4;
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

	input,
	select {
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
