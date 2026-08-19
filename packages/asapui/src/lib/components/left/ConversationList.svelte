<script lang="ts">
	import { tick } from 'svelte';
	import { chat } from '$lib/stores/chat.svelte';
	import { ui } from '$lib/stores/ui.svelte';

	const { searchQuery }: { searchQuery: string } = $props();

	let menuOpen    = $state<string | null>(null);
	let menuPos     = $state<{ top: number; right: number } | null>(null);
	let renameId    = $state<string | null>(null);
	let renameValue = $state('');
	let renameInput = $state<HTMLInputElement | null>(null);

	const filtered = $derived(
		chat.conversations.filter(c =>
			!searchQuery ||
			(c.title ?? '').toLowerCase().includes(searchQuery.toLowerCase()),
		),
	);

	function formatDate(iso: string) {
		const d = new Date(iso);
		const now = new Date();
		const diffDays = Math.floor((now.getTime() - d.getTime()) / 86_400_000);
		if (diffDays === 0) return 'Today';
		if (diffDays === 1) return 'Yesterday';
		if (diffDays < 7)  return `${diffDays} days ago`;
		return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
	}

	function openMenu(e: MouseEvent, id: string) {
		e.stopPropagation();
		if (menuOpen === id) { closeMenu(); return; }
		const btn = e.currentTarget as HTMLElement;
		const rect = btn.getBoundingClientRect();
		const DROPDOWN_H = 90; // approx height of the 2-item dropdown
		const top = rect.bottom + DROPDOWN_H > window.innerHeight
			? rect.top - DROPDOWN_H - 4  // flip above when too close to bottom
			: rect.bottom + 4;
		menuPos = { top, right: window.innerWidth - rect.right };
		menuOpen = id;
	}

	function closeMenu() {
		menuOpen = null;
		menuPos  = null;
	}

	async function startRename(id: string) {
		const conv = chat.conversations.find(c => c.id === id);
		closeMenu();
		renameId    = id;
		renameValue = conv?.title ?? '';
		await tick();
		renameInput?.focus();
		renameInput?.select();
	}

	async function confirmRename() {
		if (!renameId) return;
		const trimmed = renameValue.trim();
		if (trimmed) await chat.renameConversation(renameId, trimmed);
		renameId = null;
	}

	function cancelRename() {
		renameId    = null;
		renameValue = '';
	}

	async function deleteConversation(id: string) {
		closeMenu();
		await chat.deleteConversation(id);
	}
</script>

<!-- Backdrop + fixed dropdown — escapes the overflow-y-auto scroll container -->
{#if menuOpen && menuPos}
	<div class="fixed inset-0 z-40" onpointerdown={() => closeMenu()}></div>
	<div
		class="fixed z-50 bg-white border border-sand rounded-lg shadow-lg py-1 w-36"
		style="top: {menuPos.top}px; right: {menuPos.right}px"
		onpointerdown={(e) => e.stopPropagation()}
	>
		<button
			onclick={() => startRename(menuOpen!)}
			class="w-full flex items-center gap-2 px-3 py-2 text-sm text-charcoal hover:bg-parchment text-left"
		>
			<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536M9 13l6.768-6.768a2 2 0 0 1 2.829 2.829L11.829 15.83 8 17l1.171-3.829z" />
			</svg>
			Rename
		</button>
		<button
			onclick={() => deleteConversation(menuOpen!)}
			class="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-600 hover:bg-red-50 text-left"
		>
			<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0 1 16.138 21H7.862a2 2 0 0 1-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v3M4 7h16" />
			</svg>
			<span class="font-medium">Delete</span>
		</button>
	</div>
{/if}

<div class="flex flex-col gap-0.5 overflow-y-auto flex-1 min-h-0 px-2 pb-2">
	{#if filtered.length === 0}
		<p class="text-xs text-muted-light text-center py-6 italic">
			{searchQuery ? 'No matching chats' : 'No chats yet'}
		</p>
	{:else}
		{#each filtered as conv (conv.id)}
			{#if renameId === conv.id}
				<div class="px-1 py-1">
					<input
						bind:this={renameInput}
						bind:value={renameValue}
						onkeydown={(e) => {
							if (e.key === 'Enter')  confirmRename();
							if (e.key === 'Escape') cancelRename();
						}}
						onblur={confirmRename}
						class="w-full text-sm bg-white border border-navy/40 rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-navy/30"
					/>
				</div>
			{:else}
				<div class="relative group/conv">
					<button
						onclick={() => { chat.selectConversation(conv.id); ui.setTab('chats'); }}
						class={[
							'w-full text-left pl-3 pr-8 py-2.5 rounded-lg text-sm transition-colors',
							chat.activeId === conv.id
								? 'bg-navy text-cream'
								: 'text-charcoal hover:bg-parchment',
						].join(' ')}
					>
						<div class="truncate font-medium leading-snug flex items-center gap-1.5">
							{#if chat.session?.conversationId === conv.id}
								<!-- A response is streaming into this conversation -->
								<span
									class={[
										'inline-block w-1.5 h-1.5 shrink-0 rounded-full animate-pulse',
										chat.activeId === conv.id ? 'bg-cream' : 'bg-navy',
									].join(' ')}
									title="Response in progress"
								></span>
							{/if}
							<span class="truncate">{conv.title ?? 'Untitled chat'}</span>
						</div>
						<div class={[
							'text-xs mt-0.5 flex items-center gap-1.5 flex-wrap',
							chat.activeId === conv.id ? 'text-sand' : 'text-muted-light',
						].join(' ')}>
							{formatDate(conv.created_ts)}
							{#if conv.message_count > 0}
								· {conv.message_count} msg{conv.message_count !== 1 ? 's' : ''}
							{/if}
							{#if conv.context_pct != null}
								<span class={[
									'inline-block px-1.5 rounded font-medium tabular-nums',
									chat.activeId === conv.id
										? 'bg-white/15 text-cream'
										: conv.context_pct >= 75 ? 'bg-red-50 text-red-600'
										: conv.context_pct >= 50 ? 'bg-amber-50 text-amber-700'
										: 'bg-navy/10 text-navy',
								].join(' ')}>
									{conv.context_pct}%
								</span>
							{/if}
						</div>
					</button>

					<!-- 3-dot kebab — visible on hover or when this menu is open -->
					<button
						onclick={(e) => openMenu(e, conv.id)}
						class={[
							'absolute right-1 top-1/2 -translate-y-1/2 p-1.5 rounded-md transition-opacity',
							(chat.activeId === conv.id || menuOpen === conv.id)
								? 'opacity-100'
								: 'opacity-0 group-hover/conv:opacity-100',
							chat.activeId === conv.id
								? 'text-sand hover:text-cream hover:bg-white/10'
								: 'text-muted-light hover:text-charcoal hover:bg-sand',
						].join(' ')}
						aria-label="Conversation options"
					>
						<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 24 24">
							<circle cx="12" cy="5"  r="1.5"/>
							<circle cx="12" cy="12" r="1.5"/>
							<circle cx="12" cy="19" r="1.5"/>
						</svg>
					</button>
				</div>
			{/if}
		{/each}
	{/if}
</div>
