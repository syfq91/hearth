<script lang="ts">
	import { api } from '../../../lib/api/client';
	import { invalidateAll } from '$app/navigation';
	import { session } from '../../../lib/auth.svelte';
	import type { LibrarySummary, UserDetail } from '../../../lib/api/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	let users = $state<UserDetail[]>([]);
	let libraries = $state<LibrarySummary[]>([]);
	$effect(() => {
		users = data.users;
		libraries = data.libraries;
	});

	let message = $state<string | null>(null);
	let error = $state<string | null>(null);

	/* ---- create ---- */
	let newUsername = $state('');
	let newPassword = $state('');
	let newRole = $state<'user' | 'admin'>('user');
	let newAllowTranscode = $state(true);
	let newLibraryIds = $state<string[]>([]);
	let creating = $state(false);

	async function createUser(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		if (!newUsername.trim() || !newPassword) return;
		creating = true;
		message = null;
		error = null;
		try {
			await api.post('/users', {
				body: {
					username: newUsername.trim(),
					password: newPassword,
					role: newRole,
					allow_transcode: newAllowTranscode,
					library_ids: newRole === 'admin' ? [] : newLibraryIds
				}
			});
			message = `User “${newUsername.trim()}” created.`;
			newUsername = '';
			newPassword = '';
			newRole = 'user';
			newAllowTranscode = true;
			newLibraryIds = [];
			await invalidateAll();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			creating = false;
		}
	}

	/* ---- update ---- */
	let editingId = $state<string | null>(null);
	let editRole = $state<'user' | 'admin'>('user');
	let editDisabled = $state(false);
	let editAllowTranscode = $state(true);
	let editLibraryIds = $state<string[]>([]);
	let editBusy = $state(false);

	function startEdit(user: UserDetail): void {
		editingId = user.id;
		editRole = user.role as 'user' | 'admin';
		editDisabled = user.disabled;
		editAllowTranscode = user.allow_transcode;
		editLibraryIds = [...user.library_ids];
	}

	async function saveEdit(userId: string): Promise<void> {
		editBusy = true;
		error = null;
		try {
			await api.patch(`/users/${userId}`, {
				body: {
					role: editRole,
					disabled: editDisabled,
					allow_transcode: editAllowTranscode,
					library_ids: editRole === 'admin' ? [] : editLibraryIds
				}
			});
			message = 'User updated.';
			editingId = null;
			await invalidateAll();
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			editBusy = false;
		}
	}

	/* ---- password reset ---- */
	let resetId = $state<string | null>(null);
	let resetPassword = $state('');
	let resetBusy = $state(false);

	async function resetPasswordFor(userId: string): Promise<void> {
		if (!resetPassword) return;
		resetBusy = true;
		error = null;
		try {
			await api.post(`/users/${userId}/password`, { body: { password: resetPassword } });
			message = 'Password reset.';
			resetId = null;
			resetPassword = '';
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		} finally {
			resetBusy = false;
		}
	}

	async function removeUser(user: UserDetail): Promise<void> {
		// There is no user-delete endpoint; disabling is the supported way to
		// retire an account (PATCH /users/{id} with disabled=true).
		if (!confirm(`Disable “${user.username}”? They will no longer be able to sign in.`)) return;
		error = null;
		try {
			await api.patch(`/users/${user.id}`, { body: { disabled: true } });
			users = users.map((u) => (u.id === user.id ? { ...u, disabled: true } : u));
			message = `User “${user.username}” disabled.`;
		} catch (e) {
			error = e instanceof Error ? e.message : String(e);
		}
	}
</script>

<svelte:head>
	<title>Users · Admin · Hearth</title>
</svelte:head>

<section class="card" aria-labelledby="create-heading">
	<h2 id="create-heading">Add a user</h2>
	<form class="create-form" onsubmit={createUser}>
		<div class="row">
			<label>
				<span>Username</span>
				<input type="text" bind:value={newUsername} required maxlength={64} autocomplete="off" />
			</label>
			<label>
				<span>Password</span>
				<input type="password" bind:value={newPassword} required autocomplete="new-password" />
			</label>
			<label>
				<span>Role</span>
				<select bind:value={newRole}>
					<option value="user">User</option>
					<option value="admin">Admin</option>
				</select>
			</label>
			<label class="check">
				<input type="checkbox" bind:checked={newAllowTranscode} />
				<span>Allow transcoding</span>
			</label>
		</div>

		{#if newRole === 'user'}
			<fieldset class="libraries-fieldset">
				<legend>Library access</legend>
				{#if libraries.length === 0}
					<p class="muted">No libraries created yet.</p>
				{:else}
					<div class="checkbox-grid">
						{#each libraries as lib (lib.id)}
							<label class="check">
								<input
									type="checkbox"
									checked={newLibraryIds.includes(lib.id)}
									onchange={(e) => {
										if (e.currentTarget.checked) {
											newLibraryIds = [...newLibraryIds, lib.id];
										} else {
											newLibraryIds = newLibraryIds.filter((id) => id !== lib.id);
										}
									}}
								/>
								<span>{lib.name} <small class="muted">({lib.type})</small></span>
							</label>
						{/each}
					</div>
				{/if}
			</fieldset>
		{/if}

		<div>
			<button type="submit" disabled={creating}>{creating ? 'Creating…' : 'Create user'}</button>
		</div>
	</form>
</section>

{#if message}
	<p class="ok" role="status">{message}</p>
{/if}
{#if error}
	<p class="err" role="alert">{error}</p>
{/if}

<section class="card" aria-labelledby="users-heading">
	<h2 id="users-heading">Users</h2>
	<ul class="list">
		{#each users as user (user.id)}
			<li class="user">
				<div class="user-meta">
					<div class="head">
						<strong>{user.username}</strong>
						<span class="chip">{user.role}</span>
						{#if user.disabled}<span class="chip disabled">disabled</span>{/if}
						{#if !user.allow_transcode}<span class="chip warn">transcoding disabled</span>{/if}
						{#if user.listenbrainz_configured}<span class="chip">listenbrainz</span>{/if}
					</div>
					<div class="lib-row">
						<span class="muted lib-label">Libraries:</span>
						{#if user.role === 'admin'}
							<span class="chip">All libraries (Admin)</span>
						{:else if user.library_ids.length === 0}
							<span class="chip warn">No library access</span>
						{:else}
							{#each user.library_ids as libId}
								{@const lib = libraries.find((l) => l.id === libId)}
								<span class="chip">{lib ? lib.name : libId}</span>
							{/each}
						{/if}
					</div>
				</div>

				{#if editingId === user.id}
					<form
						class="edit"
						onsubmit={(event) => {
							event.preventDefault();
							void saveEdit(user.id);
						}}
					>
						<div class="row">
							<label>
								<span>Role</span>
								<select bind:value={editRole}>
									<option value="user">User</option>
									<option value="admin">Admin</option>
								</select>
							</label>
							<label class="check">
								<input type="checkbox" bind:checked={editDisabled} />
								<span>Disabled</span>
							</label>
							<label class="check">
								<input type="checkbox" bind:checked={editAllowTranscode} />
								<span>Allow transcoding</span>
							</label>
						</div>

						{#if editRole === 'user'}
							<fieldset class="libraries-fieldset">
								<legend>Library access</legend>
								{#if libraries.length === 0}
									<p class="muted">No libraries created yet.</p>
								{:else}
									<div class="checkbox-grid">
										{#each libraries as lib (lib.id)}
											<label class="check">
												<input
													type="checkbox"
													checked={editLibraryIds.includes(lib.id)}
													onchange={(e) => {
														if (e.currentTarget.checked) {
															editLibraryIds = [...editLibraryIds, lib.id];
														} else {
															editLibraryIds = editLibraryIds.filter((id) => id !== lib.id);
														}
													}}
												/>
												<span>{lib.name} <small class="muted">({lib.type})</small></span>
											</label>
										{/each}
									</div>
								{/if}
							</fieldset>
						{/if}

						<div class="actions">
							<button type="submit" disabled={editBusy}>Save</button>
							<button type="button" class="ghost" onclick={() => (editingId = null)}>Cancel</button>
						</div>
					</form>
				{:else}
					<div class="actions">
						<button type="button" class="ghost" onclick={() => startEdit(user)}>Edit</button>
						{#if resetId === user.id}
							<form
								class="reset"
								onsubmit={(event) => {
									event.preventDefault();
									void resetPasswordFor(user.id);
								}}
							>
								<label>
									<span>New password</span>
									<input
										type="password"
										bind:value={resetPassword}
										required
										autocomplete="new-password"
									/>
								</label>
								<button type="submit" disabled={resetBusy}>Reset</button>
								<button type="button" class="ghost" onclick={() => (resetId = null)}>Cancel</button>
							</form>
						{:else}
							<button type="button" class="ghost" onclick={() => (resetId = user.id)}>
								Reset password
							</button>
						{/if}
						{#if user.id !== session.user?.id}
							<button type="button" class="ghost danger" onclick={() => removeUser(user)}>
								{user.disabled ? 'Already disabled' : 'Disable'}
							</button>
							{#if user.disabled}
								<button
									type="button"
									class="ghost"
									onclick={() => {
										void api
											.patch(`/users/${user.id}`, { body: { disabled: false } })
											.then(() => {
												users = users.map((u) =>
													u.id === user.id ? { ...u, disabled: false } : u
												);
											});
									}}
								>
									Enable
								</button>
							{/if}
						{/if}
					</div>
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

	.create-form {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}

	.row {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 0.75rem;
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
		padding: 0.45rem 0.6rem;
		color: var(--text);
		font: inherit;
	}

	.check {
		flex-direction: row;
		align-items: center;
		gap: 0.4rem;
		padding-bottom: 0.5rem;
		cursor: pointer;
	}

	.libraries-fieldset {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.75rem 1rem;
		background: var(--surface-2);
	}

	.libraries-fieldset legend {
		font-size: 0.85rem;
		color: var(--muted);
		padding: 0 0.35rem;
	}

	.checkbox-grid {
		display: flex;
		flex-wrap: wrap;
		gap: 1rem;
		margin-top: 0.25rem;
	}

	.checkbox-grid .check {
		padding-bottom: 0;
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

	button:disabled {
		opacity: 0.6;
		cursor: default;
	}

	.list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 0.75rem;
	}

	.user {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.85rem;
	}

	.user-meta {
		display: flex;
		flex-direction: column;
		gap: 0.35rem;
		margin-bottom: 0.65rem;
	}

	.head {
		display: flex;
		align-items: center;
		gap: 0.35rem;
	}

	.lib-row {
		display: flex;
		align-items: center;
		flex-wrap: wrap;
		gap: 0.35rem;
		font-size: 0.85rem;
	}

	.lib-label {
		font-size: 0.8rem;
		margin-right: 0.2rem;
	}

	.chip {
		font-size: 0.72rem;
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0.1rem 0.5rem;
	}

	.chip.disabled {
		border-color: var(--danger);
		color: var(--danger);
	}

	.chip.warn {
		border-color: var(--warn, #e6a23c);
		color: var(--warn, #e6a23c);
	}

	.actions,
	.edit,
	.reset {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}

	.actions {
		flex-direction: row;
		flex-wrap: wrap;
		align-items: flex-end;
		gap: 0.5rem;
	}

	.ghost {
		background: var(--surface-2);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.4rem 0.85rem;
		cursor: pointer;
	}

	.ghost:hover:not(:disabled) {
		background: var(--surface);
	}

	.danger {
		border-color: var(--danger);
		color: var(--danger);
	}

	.ok {
		color: var(--accent);
		font-size: 0.9rem;
	}

	.err {
		color: var(--danger);
		font-size: 0.9rem;
	}

	.muted {
		color: var(--muted);
	}
</style>
