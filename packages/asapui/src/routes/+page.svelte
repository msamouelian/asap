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
</script>

<div class="h-screen flex flex-col overflow-hidden">
	<AppBanner />

	<div class="flex flex-1 overflow-hidden">
		<LeftPanel />
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
