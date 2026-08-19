<script lang="ts">
	import { auth } from '$lib/stores/auth.svelte';
	import { chat } from '$lib/stores/chat.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import ConversationList from './ConversationList.svelte';
	import UserPromptList from './UserPromptList.svelte';

	const adminLinks = [
		{ id: 'system-prompts' as const, label: 'Manage System Prompts', icon: '📋' },
		{ id: 'users'          as const, label: 'Manage Users',          icon: '👥' },
		{ id: 'jobs'           as const, label: 'Manage Jobs',           icon: '⚙️' },
	];

	// Neo4j Browser — served via the cluster ingress (Bolt rides the same
	// host over WebSocket, so no port-forward is needed).
	const NEO4J_URL: string = import.meta.env.VITE_NEO4J_URL ?? 'https://neo4j.localhost/';

	function handleNewAction() {
		if (ui.leftTab === 'prompts') {
			ui.openPromptForm({ mode: 'create' });
		} else {
			chat.startNewChat();
			ui.leftTab = 'chats';
		}
	}
</script>

<aside class="w-72 shrink-0 flex flex-col bg-parchment border-r border-sand h-full overflow-hidden">

	<!-- ── Administrator ───────────────────────────────────────────────── -->
	<div class="px-3 pt-4 pb-3 border-b border-sand shrink-0">
		<p class="text-[12px] font-semibold uppercase tracking-widest text-muted-light mb-2 px-1">
			Administrator
		</p>
		{#each adminLinks as link}
			<button
				onclick={() => auth.isAdmin && ui.openModal(link.id)}
				disabled={!auth.isAdmin}
				class={[
					'w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors text-left',
					auth.isAdmin
						? 'text-charcoal hover:bg-sand hover:text-navy cursor-pointer'
						: 'text-muted-light cursor-not-allowed opacity-50',
				].join(' ')}
				title={!auth.isAdmin ? 'Administrator access required' : undefined}
			>
				<span class="text-base leading-none">{link.icon}</span>
				<span>{link.label}</span>
			</button>
		{/each}
		{#if auth.isAdmin}
			<!-- External tool link — hidden entirely from non-admins -->
			<a
				href={NEO4J_URL}
				target="_blank"
				rel="noopener noreferrer"
				class="w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-sm transition-colors text-left text-charcoal hover:bg-sand hover:text-navy"
			>
				<span class="text-base leading-none">🗄️</span>
				<span>Neo4j Browser</span>
				<svg class="w-3 h-3 ml-auto text-muted-light" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24" aria-hidden="true">
					<path stroke-linecap="round" stroke-linejoin="round" d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14L21 3" />
				</svg>
			</a>
		{/if}
	</div>

	<!-- ── User ────────────────────────────────────────────────────────── -->
	<div class="px-3 pt-3 pb-1 shrink-0">
		<p class="text-[12px] font-semibold uppercase tracking-widest text-muted-light px-1">
			User
		</p>
		<!-- Effective system prompt for this session -->
		<p class="text-xs text-muted px-1 mt-1 truncate" title={auth.user?.system_prompt_title ?? 'Default (unassigned)'}>
			Prompt: {auth.user?.system_prompt_title ?? 'Default (unassigned)'}
		</p>
		<button
			onclick={() => ui.openDocuments()}
			class="w-full flex items-center gap-2.5 px-3 py-2 mt-1 rounded-lg text-sm transition-colors text-left text-charcoal hover:bg-sand hover:text-navy"
		>
			<span class="text-base leading-none">📚</span>
			<span>Document Collections</span>
		</button>
		<button
			onclick={() => ui.openHybridSearch()}
			class="w-full flex items-center gap-2.5 px-3 py-2 mt-1 rounded-lg text-sm transition-colors text-left text-charcoal hover:bg-sand hover:text-navy"
		>
			<span class="text-base leading-none">🔎</span>
			<span>Hybrid Search</span>
		</button>
	</div>

	<!-- ── New Chat / New Prompt button ───────────────────────────────── -->
	<div class="px-3 pt-1 pb-2 shrink-0">
		<button
			onclick={handleNewAction}
			class="w-full flex items-center gap-2 px-3 py-2.5 rounded-lg border border-dashed border-navy/40 text-navy text-sm font-medium hover:bg-navy hover:text-cream hover:border-navy transition-colors"
		>
			<svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4" />
			</svg>
			{ui.leftTab === 'prompts' ? 'New Prompt' : 'New Chat'}
		</button>
	</div>

	<!-- ── Tab bar ─────────────────────────────────────────────────────── -->
	<div class="flex mx-3 mb-2 bg-sand rounded-lg p-0.5 shrink-0">
		{#each [['chats', 'Chats'], ['prompts', 'User Prompts']] as [tab, label]}
			<button
				onclick={() => ui.setTab(tab as 'chats' | 'prompts')}
				class={[
					'flex-1 py-1.5 text-xs font-medium rounded-md transition-colors',
					ui.leftTab === tab
						? 'bg-white text-navy shadow-sm'
						: 'text-muted hover:text-charcoal',
				].join(' ')}
			>
				{label}
			</button>
		{/each}
	</div>

	<!-- ── Search ──────────────────────────────────────────────────────── -->
	<div class="px-3 mb-2 shrink-0">
		<div class="relative">
			<svg class="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-light" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M17 11A6 6 0 1 1 5 11a6 6 0 0 1 12 0z" />
			</svg>
			<input
				type="text"
				bind:value={ui.searchQuery}
				placeholder={ui.leftTab === 'chats' ? 'Search chats…' : 'Search prompts…'}
				class="w-full pl-8 pr-3 py-1.5 text-xs bg-white border border-sand rounded-lg focus:outline-none focus:ring-1 focus:ring-navy/30 focus:border-navy placeholder-muted-light"
			/>
		</div>
	</div>

	<!-- ── List area (fills remaining height) ─────────────────────────── -->
	<div class="flex flex-col flex-1 min-h-0 overflow-hidden">
		{#if ui.leftTab === 'chats'}
			<ConversationList searchQuery={ui.searchQuery} />
		{:else}
			<UserPromptList searchQuery={ui.searchQuery} />
		{/if}
	</div>
</aside>
