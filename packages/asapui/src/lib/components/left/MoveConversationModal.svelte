<script lang="ts">
	import type { Conversation } from '$lib/api/conversations';
	import type { Folder } from '$lib/api/folders';
	import { chat } from '$lib/stores/chat.svelte';
	import { folders } from '$lib/stores/folders.svelte';

	// Destination keys: 'root' = the implicit 'All' folder, otherwise a folder id.
	type FolderKey = string;

	const { conv, onclose }: { conv: Conversation; onclose: () => void } = $props();

	const currentKey: FolderKey = conv.folder_id ?? 'root';
	let selectedKey = $state<FolderKey | null>(null);
	let moving      = $state(false);
	let errorMsg    = $state('');

	const canMove = $derived(selectedKey !== null && selectedKey !== currentKey && !moving);

	function select(key: FolderKey) {
		if (key !== currentKey) selectedKey = key;
	}

	async function confirmMove() {
		if (!canMove || selectedKey === null) return;
		moving = true;
		errorMsg = '';
		try {
			await chat.moveConversation(conv.id, selectedKey === 'root' ? null : selectedKey);
			onclose();
		} catch (e) {
			errorMsg = e instanceof Error ? e.message : 'Failed to move conversation.';
			moving = false;
		}
	}
</script>

{#snippet folderOption(folder: Folder, depth: number)}
	{@render optionRow(folder.id, folder.name, depth)}
	{#each folders.childrenOf(folder.id) as child (child.id)}
		{@render folderOption(child, depth + 1)}
	{/each}
{/snippet}

{#snippet optionRow(key: string, name: string, depth: number)}
	{@const isCurrent  = key === currentKey}
	{@const isSelected = key === selectedKey}
	<button
		onclick={() => select(key)}
		disabled={isCurrent}
		style="padding-left: {12 + depth * 18}px"
		class={[
			'w-full flex items-center gap-2 pr-3 py-2 rounded-lg text-sm text-left transition-colors',
			isSelected
				? 'bg-navy text-cream'
				: isCurrent
					? 'text-muted-light cursor-not-allowed'
					: 'text-charcoal hover:bg-parchment',
		].join(' ')}
	>
		<span class="text-base leading-none">📁</span>
		<span class="truncate">{name}</span>
		{#if isCurrent}
			<span class="ml-auto text-xs italic shrink-0">current folder</span>
		{/if}
	</button>
{/snippet}

<div class="fixed inset-0 z-50 flex items-center justify-center bg-charcoal/50 backdrop-blur-sm">
	<button type="button" class="absolute inset-0 w-full h-full cursor-default"
		aria-label="Close modal" onclick={onclose}></button>

	<div role="dialog" aria-modal="true" aria-labelledby="move-conv-title" tabindex="-1"
		class="relative z-10 w-full mx-4 bg-cream rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[80vh] max-w-md"
	>
		<div class="px-5 pt-4 pb-3 border-b border-sand">
			<h2 id="move-conv-title" class="text-base font-semibold text-charcoal">Move conversation</h2>
			<p class="text-xs text-muted mt-0.5 truncate">
				{conv.title ?? 'Untitled chat'} — choose a destination folder
			</p>
		</div>

		<div class="flex-1 min-h-0 overflow-y-auto px-3 py-3 flex flex-col gap-0.5">
			{@render optionRow('root', 'All', 0)}
			{#each folders.childrenOf(null) as folder (folder.id)}
				{@render folderOption(folder, 1)}
			{/each}
		</div>

		{#if errorMsg}
			<p class="text-xs text-red-600 px-5 pb-2">{errorMsg}</p>
		{/if}

		<div class="px-5 py-3 border-t border-sand flex justify-end gap-2">
			<button
				onclick={onclose}
				class="px-4 py-2 rounded-lg text-sm text-charcoal hover:bg-sand transition-colors"
			>
				Cancel
			</button>
			<button
				onclick={confirmMove}
				disabled={!canMove}
				class="px-4 py-2 rounded-lg text-sm font-medium bg-navy text-cream hover:bg-navy-light transition-colors
					disabled:opacity-50 disabled:cursor-not-allowed"
			>
				{moving ? 'Moving…' : 'Move'}
			</button>
		</div>
	</div>
</div>
