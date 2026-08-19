<script lang="ts">
	import { onMount } from 'svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { usersApi, type User } from '$lib/api/users';
	import SelectSystemPromptModal from '$lib/components/admin/SelectSystemPromptModal.svelte';

	let users    = $state<User[]>([]);
	let loading  = $state(true);
	let error    = $state('');
	let updating = $state<string | null>(null); // user id being updated
	let openKebab     = $state<string | null>(null);
	let kebabDropUp   = $state(false);
	let assigningUser = $state<User | null>(null);

	const KEBAB_MENU_HEIGHT = 90;

	onMount(async () => {
		await load();
	});

	async function load() {
		loading = true;
		error = '';
		try {
			users = await usersApi.list();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load users.';
		} finally {
			loading = false;
		}
	}

	async function toggleStatus(user: User) {
		const next = user.status === 'active' ? 'inactive' : 'active';
		updating = user.id;
		try {
			const updated = await usersApi.setStatus(user.id, next);
			users = users.map(u => u.id === updated.id ? updated : u);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to update status.';
		} finally {
			updating = null;
		}
	}

	function toggleKebab(e: MouseEvent, userId: string) {
		e.stopPropagation();
		if (openKebab === userId) { openKebab = null; return; }
		const btn   = e.currentTarget as HTMLElement;
		const panel = btn.closest('[data-users-panel]');
		if (panel) {
			const btnRect   = btn.getBoundingClientRect();
			const panelRect = panel.getBoundingClientRect();
			kebabDropUp = btnRect.bottom + KEBAB_MENU_HEIGHT > panelRect.bottom;
		} else {
			kebabDropUp = false;
		}
		openKebab = userId;
	}

	function startAssign(user: User) {
		openKebab = null;
		assigningUser = user;
	}

	async function unassign(user: User) {
		openKebab = null;
		updating = user.id;
		try {
			const updated = await usersApi.assignSystemPrompt(user.id, null);
			users = users.map(u => u.id === updated.id ? updated : u);
			// Refresh own profile so the left-panel prompt label updates immediately.
			if (updated.id === auth.user?.id) await auth.loadUser();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to unassign system prompt.';
		} finally {
			updating = null;
		}
	}

	async function handleAssign(promptId: string) {
		const user = assigningUser;
		assigningUser = null;
		if (!user) return;
		updating = user.id;
		try {
			const updated = await usersApi.assignSystemPrompt(user.id, promptId);
			users = users.map(u => u.id === updated.id ? updated : u);
			// Refresh own profile so the left-panel prompt label updates immediately.
			if (updated.id === auth.user?.id) await auth.loadUser();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to assign system prompt.';
		} finally {
			updating = null;
		}
	}

	// Close kebab when clicking outside
	function handleDocClick(e: MouseEvent) {
		const target = e.target as HTMLElement;
		if (!target.closest('[data-kebab]')) openKebab = null;
	}
</script>

<svelte:document onclick={handleDocClick} />

<div class="p-6" data-users-panel>
	{#if loading}
		<div class="flex items-center justify-center py-12 text-muted text-sm">Loading users…</div>
	{:else if error}
		<div class="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700 mb-4">
			{error}
			<button onclick={load} class="ml-2 underline">Retry</button>
		</div>
	{:else if users.length === 0}
		<div class="text-center py-12 text-muted text-sm">No users yet. Users are created automatically on first login.</div>
	{:else}
		<table class="w-full text-sm">
			<thead>
				<tr class="border-b border-sand text-left">
					<th class="pb-2 font-medium text-charcoal">Name</th>
					<th class="pb-2 font-medium text-charcoal">Email</th>
					<th class="pb-2 font-medium text-charcoal">Role</th>
					<th class="pb-2 font-medium text-charcoal">Status</th>
					<th class="pb-2 font-medium text-charcoal">System Prompt</th>
					<th class="pb-2"></th>
				</tr>
			</thead>
			<tbody>
				{#each users as user (user.id)}
					<tr class="border-b border-sand/50 hover:bg-sand/20 transition-colors">
						<td class="py-2.5 pr-4 text-navy font-medium">{user.full_name}</td>
						<td class="py-2.5 pr-4 text-charcoal">{user.email}</td>
						<td class="py-2.5 pr-4">
							<span class="text-xs font-medium px-2 py-0.5 rounded-full
								{user.role === 'admin' ? 'bg-crimson/10 text-crimson' : 'bg-navy/10 text-navy'}">
								{user.role}
							</span>
						</td>
						<td class="py-2.5 pr-4">
							<span class="text-xs font-medium px-2 py-0.5 rounded-full
								{user.status === 'active' ? 'bg-emerald-50 text-emerald-700' : 'bg-red-50 text-red-700'}">
								{user.status}
							</span>
						</td>
						<td class="py-2.5 pr-4">
							{#if user.system_prompt_title}
								<span class="text-xs text-charcoal">{user.system_prompt_title}</span>
							{:else}
								<span class="text-xs font-medium px-2 py-0.5 rounded-full bg-sand/60 text-muted">Unassigned</span>
							{/if}
						</td>
						<td class="py-2.5 text-right whitespace-nowrap">
							<button
								onclick={() => toggleStatus(user)}
								disabled={updating === user.id}
								class="text-xs px-3 py-1 rounded-lg border transition-colors disabled:opacity-50
									{user.status === 'active'
										? 'border-red-200 text-red-700 hover:bg-red-50'
										: 'border-emerald-200 text-emerald-700 hover:bg-emerald-50'}"
							>
								{updating === user.id ? '…' : user.status === 'active' ? 'Deactivate' : 'Activate'}
							</button>
							<span class="relative inline-block align-middle ml-1" data-kebab>
								<button
									onclick={(e) => toggleKebab(e, user.id)}
									class="p-1 rounded text-muted-light hover:text-charcoal hover:bg-sand transition-colors"
									aria-label="User actions"
								>
									<svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
										<path d="M10 6a2 2 0 110-4 2 2 0 010 4zm0 6a2 2 0 110-4 2 2 0 010 4zm0 6a2 2 0 110-4 2 2 0 010 4z"/>
									</svg>
								</button>
								{#if openKebab === user.id}
									<div
										class={[
											'absolute right-0 z-20 bg-white border border-sand rounded-lg shadow-lg py-1 w-48 whitespace-normal',
											kebabDropUp ? 'bottom-7' : 'top-7',
										].join(' ')}
										data-kebab
									>
										<button
											onclick={() => startAssign(user)}
											class="w-full text-left px-3 py-1.5 text-xs hover:bg-parchment transition-colors text-charcoal"
										>
											Assign System Prompt
										</button>
										<button
											onclick={() => unassign(user)}
											disabled={!user.system_prompt_id}
											class="w-full text-left px-3 py-1.5 text-xs transition-colors
												{user.system_prompt_id
													? 'hover:bg-parchment text-charcoal'
													: 'text-muted-light cursor-not-allowed'}"
										>
											Unassign System Prompt
										</button>
									</div>
								{/if}
							</span>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
		<p class="text-xs text-muted-light mt-4">
			{users.length} user{users.length !== 1 ? 's' : ''} total.
			To change roles, use the Keycloak admin console at
			<a href="http://keycloak.localhost/admin" target="_blank" class="underline hover:text-charcoal">keycloak.localhost/admin</a>.
		</p>
	{/if}
</div>

{#if assigningUser}
	<SelectSystemPromptModal
		userName={assigningUser.full_name}
		currentPromptId={assigningUser.system_prompt_id}
		onselect={handleAssign}
		oncancel={() => assigningUser = null}
	/>
{/if}
