<script lang="ts">
	import { onMount } from 'svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { chat } from '$lib/stores/chat.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import AppBanner from '$lib/components/AppBanner.svelte';
	import LeftPanel from '$lib/components/left/LeftPanel.svelte';
	import ChatPanel from '$lib/components/chat/ChatPanel.svelte';
	import AdminModal from '$lib/components/modals/AdminModal.svelte';
	import DocumentsModal from '$lib/components/modals/DocumentsModal.svelte';
	import HybridSearchModal from '$lib/components/modals/HybridSearchModal.svelte';
	import PromptFormModal from '$lib/components/modals/PromptFormModal.svelte';

	onMount(async () => {
		if (!auth.isAuthenticated) return; // layout.svelte handles Keycloak redirect
		await Promise.all([
			auth.loadUser(),
			chat.loadConversations(),
		]);
	});

	// Left-panel resize. Pointer capture keeps the drag alive even when the
	// cursor outruns the 6px handle; the panel starts at the viewport's left
	// edge, so clientX IS the desired width (the store clamps it).
	let resizing = $state(false);

	function startResize(e: PointerEvent) {
		e.preventDefault();
		(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
		resizing = true;
	}
	function moveResize(e: PointerEvent) {
		if (resizing) ui.setLeftPanelWidth(e.clientX);
	}
	function endResize(e: PointerEvent) {
		resizing = false;
		(e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
	}
</script>

<div class="h-screen flex flex-col overflow-hidden">
	<AppBanner />

	<div class="flex flex-1 overflow-hidden" class:select-none={resizing}>
		<LeftPanel />
		<!-- Drag handle: a 6px hit area straddling the panel's 1px border
		     (3px each side, so it doesn't sit over the conversation-list
		     scrollbar). Double-click restores the default width. -->
		<div
			role="separator"
			aria-orientation="vertical"
			aria-label="Resize sidebar"
			class={[
				'w-1.5 -ml-[3px] -mr-[3px] shrink-0 cursor-col-resize transition-colors z-10',
				resizing ? 'bg-navy/40' : 'bg-transparent hover:bg-navy/25',
			].join(' ')}
			style="touch-action: none"
			onpointerdown={startResize}
			onpointermove={moveResize}
			onpointerup={endResize}
			onpointercancel={endResize}
			ondblclick={() => ui.resetLeftPanelWidth()}
		></div>
		<main class="flex-1 overflow-hidden">
			<ChatPanel />
		</main>
	</div>
</div>

{#if ui.documentsOpen}
	<DocumentsModal />
{/if}
{#if ui.hybridSearchOpen}
	<HybridSearchModal />
{/if}

{#if ui.adminModal}
	<AdminModal modal={ui.adminModal} />
{/if}

<PromptFormModal />
