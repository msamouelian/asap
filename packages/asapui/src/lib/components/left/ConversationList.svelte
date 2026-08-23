<script lang="ts">
	import { onMount, tick } from 'svelte';
	import type { Conversation } from '$lib/api/conversations';
	import type { Folder } from '$lib/api/folders';
	import { chat } from '$lib/stores/chat.svelte';
	import { folders } from '$lib/stores/folders.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import MoveConversationModal from './MoveConversationModal.svelte';

	const { searchQuery }: { searchQuery: string } = $props();

	// ── Sort (persisted) ──────────────────────────────────────────────────────
	type SortField = 'title' | 'date';
	type SortDir   = 'asc' | 'desc';
	const K_SORT = 'asap_conv_sort';

	function storedSort(): { field: SortField; dir: SortDir } {
		try {
			const s = JSON.parse(localStorage.getItem(K_SORT) ?? '');
			if ((s.field === 'title' || s.field === 'date') && (s.dir === 'asc' || s.dir === 'desc')) return s;
		} catch { /* fall through */ }
		return { field: 'date', dir: 'desc' };
	}
	let sortField = $state<SortField>(storedSort().field);
	let sortDir   = $state<SortDir>(storedSort().dir);

	function toggleSort(field: SortField) {
		if (sortField === field) {
			sortDir = sortDir === 'asc' ? 'desc' : 'asc';
		} else {
			sortField = field;
			sortDir   = field === 'title' ? 'asc' : 'desc';
		}
		try { localStorage.setItem(K_SORT, JSON.stringify({ field: sortField, dir: sortDir })); } catch { /* non-fatal */ }
	}

	function convCompare(a: Conversation, b: Conversation): number {
		const r = sortField === 'title'
			? (a.title ?? 'Untitled chat').localeCompare(b.title ?? 'Untitled chat', undefined, { sensitivity: 'base' })
			: new Date(a.created_ts).getTime() - new Date(b.created_ts).getTime();
		return sortDir === 'asc' ? r : -r;
	}

	// ── Folder tree helpers ───────────────────────────────────────────────────
	// Conversations whose folder no longer exists (defensive) fall back to 'All'.
	const knownFolderIds = $derived(new Set(folders.folders.map(f => f.id)));

	function convsIn(folderId: string | null): Conversation[] {
		return chat.conversations
			.filter(c => folderId === null
				? c.folder_id === null || !knownFolderIds.has(c.folder_id)
				: c.folder_id === folderId)
			.sort(convCompare);
	}

	function folderIsEmpty(id: string): boolean {
		return folders.childrenOf(id).length === 0 && convsIn(id).length === 0;
	}

	// ── Expanded folders (persisted; the root 'All' is open by default) ──────
	const K_OPEN = 'asap_conv_folders_open';
	function storedOpen(): Set<string> {
		try {
			const arr = JSON.parse(localStorage.getItem(K_OPEN) ?? '');
			if (Array.isArray(arr)) return new Set(arr.concat('root'));
		} catch { /* fall through */ }
		return new Set(['root']);
	}
	let open = $state<Set<string>>(storedOpen());

	function isOpen(key: string): boolean { return open.has(key); }
	function setOpen(key: string, value: boolean) {
		const next = new Set(open);
		if (value) next.add(key); else next.delete(key);
		open = next;
		try { localStorage.setItem(K_OPEN, JSON.stringify([...next])); } catch { /* non-fatal */ }
	}
	function toggleOpen(key: string) { setOpen(key, !open.has(key)); }

	// ── Search (flat view across all folders) ────────────────────────────────
	const searching = $derived(searchQuery.trim().length > 0);
	const searchResults = $derived(
		chat.conversations
			.filter(c => (c.title ?? '').toLowerCase().includes(searchQuery.toLowerCase()))
			.sort(convCompare),
	);

	// ── Transient error line (folder ops: duplicates, non-empty delete) ──────
	let errorMsg = $state('');
	let errorTimer: ReturnType<typeof setTimeout> | null = null;
	function showError(e: unknown, fallback: string) {
		errorMsg = e instanceof Error ? e.message : fallback;
		if (errorTimer) clearTimeout(errorTimer);
		errorTimer = setTimeout(() => { errorMsg = ''; }, 5000);
	}

	// ── Folder selection ──────────────────────────────────────────────────────
	// 'root' = the 'All' folder, otherwise a folder id. Selection gives the
	// user an unambiguous answer to "which folder am I acting on?" — clicking
	// or right-clicking a folder selects it, and context-menu actions (rename,
	// delete, paste) always target the highlighted folder.
	let selectedFolderKey = $state<string | null>(null);

	// ── Folder context menu (right-click) ─────────────────────────────────────
	// folderId null = the root 'All' (New Folder / Paste only).
	let folderMenu = $state<{ folderId: string | null; x: number; y: number } | null>(null);

	function openFolderMenu(e: MouseEvent, folderId: string | null) {
		e.preventDefault();
		e.stopPropagation();
		closeConvMenu();
		selectedFolderKey = folderId ?? 'root';
		folderMenu = {
			folderId,
			x: Math.min(e.clientX, window.innerWidth - 170),
			y: Math.min(e.clientY, window.innerHeight - 180),
		};
	}
	function closeFolderMenu() { folderMenu = null; }

	function clickFolder(key: string) {
		selectedFolderKey = key;
		toggleOpen(key);
	}

	// ── Cut & paste (move without dragging across a long list) ───────────────
	let cutConvId = $state<string | null>(null);

	function cutConversation(id: string) {
		closeConvMenu();
		cutConvId = id;
	}

	async function pasteInto(folderId: string | null) {
		closeFolderMenu();
		const id = cutConvId;
		cutConvId = null;
		if (!id || !chat.conversations.find(c => c.id === id)) return;
		try {
			await chat.moveConversation(id, folderId);
		} catch (e) {
			showError(e, 'Failed to move conversation.');
		}
	}

	// True when pasting into this folder would actually move the cut conversation.
	function canPasteInto(folderId: string | null): boolean {
		if (!cutConvId) return false;
		const conv = chat.conversations.find(c => c.id === cutConvId);
		return !!conv && conv.folder_id !== folderId;
	}

	// ── "Move to…" modal ──────────────────────────────────────────────────────
	let moveConv = $state<Conversation | null>(null);

	function startMove(id: string) {
		closeConvMenu();
		moveConv = chat.conversations.find(c => c.id === id) ?? null;
	}

	// ── Folder create / rename (inline inputs) ────────────────────────────────
	let createParent  = $state<string | null | undefined>(undefined); // undefined = not creating
	let createValue   = $state('');
	let createInput   = $state<HTMLInputElement | null>(null);
	let folderRenameId    = $state<string | null>(null);
	let folderRenameValue = $state('');
	let folderRenameInput = $state<HTMLInputElement | null>(null);

	async function startCreateFolder(parentId: string | null) {
		closeFolderMenu();
		if (parentId !== null) setOpen(parentId, true);
		createParent = parentId;
		createValue  = '';
		await tick();
		createInput?.focus();
	}

	async function confirmCreateFolder() {
		if (createParent === undefined) return;
		const name = createValue.trim();
		const parent = createParent;
		createParent = undefined;
		if (!name) return;
		try {
			await folders.create(name, parent);
		} catch (e) {
			showError(e, 'Failed to create folder.');
		}
	}

	async function startRenameFolder(id: string) {
		closeFolderMenu();
		folderRenameId    = id;
		folderRenameValue = folders.folders.find(f => f.id === id)?.name ?? '';
		await tick();
		folderRenameInput?.focus();
		folderRenameInput?.select();
	}

	async function confirmRenameFolder() {
		if (!folderRenameId) return;
		const id = folderRenameId;
		const name = folderRenameValue.trim();
		folderRenameId = null;
		if (!name) return;
		try {
			await folders.rename(id, name);
		} catch (e) {
			showError(e, 'Failed to rename folder.');
		}
	}

	async function deleteFolder(id: string) {
		closeFolderMenu();
		try {
			await folders.remove(id);
		} catch (e) {
			showError(e, 'Failed to delete folder.');
		}
	}

	// ── Drag & drop: conversations onto folders ───────────────────────────────
	// dragOverKey: 'root' or a folder id, while a dragged conversation hovers it.
	let dragOverKey = $state<string | null>(null);

	function onConvDragStart(e: DragEvent, convId: string) {
		e.dataTransfer?.setData('application/x-asap-conversation', convId);
		if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
	}
	function onFolderDragOver(e: DragEvent, key: string) {
		if (!e.dataTransfer?.types.includes('application/x-asap-conversation')) return;
		e.preventDefault();
		e.dataTransfer.dropEffect = 'move';
		dragOverKey = key;
	}
	async function onFolderDrop(e: DragEvent, folderId: string | null) {
		e.preventDefault();
		dragOverKey = null;
		const convId = e.dataTransfer?.getData('application/x-asap-conversation');
		if (!convId) return;
		try {
			await chat.moveConversation(convId, folderId);
		} catch (err) {
			showError(err, 'Failed to move conversation.');
		}
	}

	// ── Conversation menu (kebab click or right-click) / rename / delete ─────
	const CONV_MENU_W = 144; // w-36
	const CONV_MENU_H = 165; // approx height of the 4-item dropdown
	let menuOpen    = $state<string | null>(null);
	let menuPos     = $state<{ top: number; left: number } | null>(null);
	let renameId    = $state<string | null>(null);
	let renameValue = $state('');
	let renameInput = $state<HTMLInputElement | null>(null);

	function openConvMenu(e: MouseEvent, id: string) {
		e.stopPropagation();
		closeFolderMenu();
		if (menuOpen === id) { closeConvMenu(); return; }
		const btn = e.currentTarget as HTMLElement;
		const rect = btn.getBoundingClientRect();
		const top = rect.bottom + CONV_MENU_H > window.innerHeight
			? rect.top - CONV_MENU_H - 4  // flip above when too close to bottom
			: rect.bottom + 4;
		menuPos = { top, left: Math.max(4, rect.right - CONV_MENU_W) };
		menuOpen = id;
	}

	// Right-click anywhere on a conversation row — same menu, at the pointer.
	function openConvContextMenu(e: MouseEvent, id: string) {
		e.preventDefault();
		e.stopPropagation();
		closeFolderMenu();
		menuPos = {
			top:  Math.min(e.clientY, window.innerHeight - CONV_MENU_H - 8),
			left: Math.min(e.clientX, window.innerWidth - CONV_MENU_W - 8),
		};
		menuOpen = id;
	}

	function closeConvMenu() {
		menuOpen = null;
		menuPos  = null;
	}

	async function startRename(id: string) {
		const conv = chat.conversations.find(c => c.id === id);
		closeConvMenu();
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
		closeConvMenu();
		await chat.deleteConversation(id);
	}

	// ── Formatting ────────────────────────────────────────────────────────────
	function fmtDate(iso: string): string {
		return new Date(iso).toLocaleDateString(undefined, {
			month: 'long', day: 'numeric', year: 'numeric',
		});
	}

	function indentPx(depth: number): string {
		return `${depth * 14}px`;
	}

	// Escape cancels a pending cut and closes any open menu.
	function onWindowKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			cutConvId = null;
			closeConvMenu();
			closeFolderMenu();
		}
	}

	onMount(() => {
		if (!folders.loaded) folders.load().catch(e => showError(e, 'Failed to load folders.'));
	});
</script>

<svelte:window onkeydown={onWindowKeydown} />

<!-- ── Conversation menu (fixed — escapes the scroll container) ────────────── -->
{#if menuOpen && menuPos}
	<div
		class="fixed inset-0 z-40"
		onpointerdown={() => closeConvMenu()}
		oncontextmenu={(e) => { e.preventDefault(); closeConvMenu(); }}
	></div>
	<div
		class="fixed z-50 bg-white border border-sand rounded-lg shadow-lg py-1 w-36"
		style="top: {menuPos.top}px; left: {menuPos.left}px"
		onpointerdown={(e) => e.stopPropagation()}
	>
		<button
			onclick={() => startMove(menuOpen!)}
			class="w-full flex items-center gap-2 px-3 py-2 text-sm text-charcoal hover:bg-parchment text-left"
		>
			<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7zM11 14h6m0 0l-2.5-2.5M17 14l-2.5 2.5" />
			</svg>
			Move to…
		</button>
		<button
			onclick={() => cutConversation(menuOpen!)}
			class="w-full flex items-center gap-2 px-3 py-2 text-sm text-charcoal hover:bg-parchment text-left"
		>
			<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M7.85 9.7a3 3 0 1 1 1.84-1.85M7.85 9.7L19 21M7.85 9.7l3.02-3.06M9.69 16.16a3 3 0 1 1-1.84-1.85m1.84 1.85L19 7M9.69 16.16l3.06-3.02" />
			</svg>
			Cut
		</button>
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

<!-- ── Folder context menu (right-click) ───────────────────────────────────── -->
{#if folderMenu}
	{@const isRoot = folderMenu.folderId === null}
	{@const empty  = isRoot || folderIsEmpty(folderMenu.folderId!)}
	<div
		class="fixed inset-0 z-40"
		onpointerdown={() => closeFolderMenu()}
		oncontextmenu={(e) => { e.preventDefault(); closeFolderMenu(); }}
	></div>
	<div
		class="fixed z-50 bg-white border border-sand rounded-lg shadow-lg py-1 w-40"
		style="top: {folderMenu.y}px; left: {folderMenu.x}px"
		onpointerdown={(e) => e.stopPropagation()}
	>
		<button
			onclick={() => startCreateFolder(folderMenu!.folderId)}
			class="w-full flex items-center gap-2 px-3 py-2 text-sm text-charcoal hover:bg-parchment text-left"
		>
			<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M12 10v6m-3-3h6M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
			</svg>
			New Folder
		</button>
		{#if cutConvId}
			{@const pasteOk = canPasteInto(folderMenu.folderId)}
			<button
				onclick={() => pasteOk && pasteInto(folderMenu!.folderId)}
				disabled={!pasteOk}
				title={!pasteOk ? 'The cut conversation is already in this folder' : undefined}
				class={[
					'w-full flex items-center gap-2 px-3 py-2 text-sm text-left',
					pasteOk ? 'text-charcoal hover:bg-parchment' : 'text-muted-light cursor-not-allowed',
				].join(' ')}
			>
				<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2M9 5a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2M9 5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2" />
				</svg>
				Paste
			</button>
		{/if}
		{#if !isRoot}
			<button
				onclick={() => startRenameFolder(folderMenu!.folderId!)}
				class="w-full flex items-center gap-2 px-3 py-2 text-sm text-charcoal hover:bg-parchment text-left"
			>
				<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536M9 13l6.768-6.768a2 2 0 0 1 2.829 2.829L11.829 15.83 8 17l1.171-3.829z" />
				</svg>
				Rename
			</button>
			<button
				onclick={() => empty && deleteFolder(folderMenu!.folderId!)}
				disabled={!empty}
				title={!empty ? 'Folder is not empty — move or delete its contents first' : undefined}
				class={[
					'w-full flex items-center gap-2 px-3 py-2 text-sm text-left',
					empty ? 'text-red-600 hover:bg-red-50' : 'text-muted-light cursor-not-allowed',
				].join(' ')}
			>
				<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0 1 16.138 21H7.862a2 2 0 0 1-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v3M4 7h16" />
				</svg>
				<span class="font-medium">Delete</span>
			</button>
		{/if}
	</div>
{/if}

<!-- ── Snippets ─────────────────────────────────────────────────────────────── -->

{#snippet convRow(conv: Conversation, depth: number)}
	{#if renameId === conv.id}
		<div class="px-1 py-1" style="margin-left: {indentPx(depth)}">
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
		<div
			class={['relative group/conv', cutConvId === conv.id ? 'opacity-50' : ''].join(' ')}
			style="margin-left: {indentPx(depth)}"
			draggable="true"
			ondragstart={(e) => onConvDragStart(e, conv.id)}
			oncontextmenu={(e) => openConvContextMenu(e, conv.id)}
			role="listitem"
		>
			<button
				onclick={() => { chat.selectConversation(conv.id); ui.setTab('chats'); }}
				class={[
					'w-full text-left pl-3 pr-8 py-2 rounded-lg text-sm transition-colors',
					chat.activeId === conv.id
						? 'bg-navy text-cream'
						: 'text-charcoal hover:bg-parchment',
				].join(' ')}
			>
				<div class="flex items-baseline gap-2">
					<div class="truncate font-medium leading-snug flex items-center gap-1.5 flex-1 min-w-0">
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
					<span class={[
						'shrink-0 text-[11px] whitespace-nowrap tabular-nums',
						chat.activeId === conv.id ? 'text-sand' : 'text-muted-light',
					].join(' ')}>
						{fmtDate(conv.created_ts)}
					</span>
				</div>
				<div class={[
					'text-xs mt-0.5 flex items-center gap-1.5 flex-wrap',
					chat.activeId === conv.id ? 'text-sand' : 'text-muted-light',
				].join(' ')}>
					{#if conv.message_count > 0}
						{conv.message_count} msg{conv.message_count !== 1 ? 's' : ''}
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
				onclick={(e) => openConvMenu(e, conv.id)}
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
{/snippet}

{#snippet createFolderInput(depth: number)}
	<div class="px-1 py-1" style="margin-left: {indentPx(depth)}">
		<input
			bind:this={createInput}
			bind:value={createValue}
			placeholder="New folder name…"
			onkeydown={(e) => {
				if (e.key === 'Enter')  confirmCreateFolder();
				if (e.key === 'Escape') createParent = undefined;
			}}
			onblur={confirmCreateFolder}
			class="w-full text-sm bg-white border border-navy/40 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-navy/30"
		/>
	</div>
{/snippet}

{#snippet folderRow(folder: Folder, depth: number)}
	{#if folderRenameId === folder.id}
		<div class="px-1 py-1" style="margin-left: {indentPx(depth)}">
			<input
				bind:this={folderRenameInput}
				bind:value={folderRenameValue}
				onkeydown={(e) => {
					if (e.key === 'Enter')  confirmRenameFolder();
					if (e.key === 'Escape') folderRenameId = null;
				}}
				onblur={confirmRenameFolder}
				class="w-full text-sm bg-white border border-navy/40 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-navy/30"
			/>
		</div>
	{:else}
		<button
			style="margin-left: {indentPx(depth)}"
			onclick={() => clickFolder(folder.id)}
			oncontextmenu={(e) => openFolderMenu(e, folder.id)}
			ondragover={(e) => onFolderDragOver(e, folder.id)}
			ondragleave={() => { if (dragOverKey === folder.id) dragOverKey = null; }}
			ondrop={(e) => onFolderDrop(e, folder.id)}
			class={[
				'w-full flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-sm text-left transition-colors',
				dragOverKey === folder.id
					? 'bg-navy/10 ring-1 ring-navy/40'
					: selectedFolderKey === folder.id
						? 'bg-sand text-navy'
						: 'text-charcoal hover:bg-parchment',
			].join(' ')}
		>
			<svg
				class={['w-3 h-3 shrink-0 text-muted-light transition-transform', isOpen(folder.id) ? 'rotate-90' : ''].join(' ')}
				fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"
			>
				<path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
			</svg>
			<span class="text-base leading-none">📁</span>
			<span class="truncate font-medium">{folder.name}</span>
		</button>
	{/if}
	{#if isOpen(folder.id)}
		{#if createParent === folder.id}
			{@render createFolderInput(depth + 1)}
		{/if}
		{#each folders.childrenOf(folder.id) as child (child.id)}
			{@render folderRow(child, depth + 1)}
		{/each}
		{#each convsIn(folder.id) as conv (conv.id)}
			{@render convRow(conv, depth + 1)}
		{/each}
	{/if}
{/snippet}

<!-- ── Sortable column headers ─────────────────────────────────────────────── -->
<div class="flex items-center px-4 pb-1 shrink-0 select-none">
	<button
		onclick={() => toggleSort('title')}
		class={[
			'flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider flex-1 text-left',
			sortField === 'title' ? 'text-navy' : 'text-muted-light hover:text-charcoal',
		].join(' ')}
	>
		Title
		{#if sortField === 'title'}<span aria-hidden="true">{sortDir === 'asc' ? '▲' : '▼'}</span>{/if}
	</button>
	<button
		onclick={() => toggleSort('date')}
		class={[
			'flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider',
			sortField === 'date' ? 'text-navy' : 'text-muted-light hover:text-charcoal',
		].join(' ')}
	>
		Date
		{#if sortField === 'date'}<span aria-hidden="true">{sortDir === 'asc' ? '▲' : '▼'}</span>{/if}
	</button>
</div>

{#if errorMsg}
	<p class="text-xs text-red-600 px-4 pb-1 shrink-0">{errorMsg}</p>
{/if}

<div class="flex flex-col gap-0.5 overflow-y-auto flex-1 min-h-0 px-2 pb-2" role="list">
	{#if searching}
		<!-- Search: flat list of matches across all folders -->
		{#if searchResults.length === 0}
			<p class="text-xs text-muted-light text-center py-6 italic">No matching chats</p>
		{:else}
			{#each searchResults as conv (conv.id)}
				{@render convRow(conv, 0)}
			{/each}
		{/if}
	{:else}
		<!-- Root folder 'All' — always present, drop target for un-filing -->
		<button
			onclick={() => clickFolder('root')}
			oncontextmenu={(e) => openFolderMenu(e, null)}
			ondragover={(e) => onFolderDragOver(e, 'root')}
			ondragleave={() => { if (dragOverKey === 'root') dragOverKey = null; }}
			ondrop={(e) => onFolderDrop(e, null)}
			class={[
				'w-full flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-sm text-left transition-colors',
				dragOverKey === 'root'
					? 'bg-navy/10 ring-1 ring-navy/40'
					: selectedFolderKey === 'root'
						? 'bg-sand text-navy'
						: 'text-charcoal hover:bg-parchment',
			].join(' ')}
		>
			<svg
				class={['w-3 h-3 shrink-0 text-muted-light transition-transform', isOpen('root') ? 'rotate-90' : ''].join(' ')}
				fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"
			>
				<path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
			</svg>
			<span class="text-base leading-none">📁</span>
			<span class="truncate font-medium">All</span>
		</button>

		{#if isOpen('root')}
			{#if createParent === null}
				{@render createFolderInput(1)}
			{/if}
			{#each folders.childrenOf(null) as folder (folder.id)}
				{@render folderRow(folder, 1)}
			{/each}
			{#each convsIn(null) as conv (conv.id)}
				{@render convRow(conv, 1)}
			{/each}
			{#if chat.conversations.length === 0 && folders.folders.length === 0}
				<p class="text-xs text-muted-light text-center py-6 italic">No chats yet</p>
			{/if}
		{/if}
	{/if}
</div>

{#if moveConv}
	<MoveConversationModal conv={moveConv} onclose={() => (moveConv = null)} />
{/if}
