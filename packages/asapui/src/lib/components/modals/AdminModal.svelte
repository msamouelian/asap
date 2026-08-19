<script lang="ts">
	import { ui, type AdminModalType } from '$lib/stores/ui.svelte';
	import ManageUsers from './ManageUsers.svelte';
	import ManageSystemPrompts from '$lib/components/admin/ManageSystemPrompts.svelte';
	import ManageJobs from '$lib/components/admin/ManageJobs.svelte';

	const { modal }: { modal: AdminModalType } = $props();

	const titles: Record<AdminModalType, string> = {
		'system-prompts': 'Manage System Prompts',
		'users':          'Manage Users',
		'jobs':           'Manage Jobs',
	};

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') ui.closeModal();
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<div class="fixed inset-0 z-50 flex items-center justify-center bg-charcoal/50 backdrop-blur-sm">
	<button
		type="button"
		class="absolute inset-0 w-full h-full cursor-default"
		aria-label="Close modal"
		onclick={() => ui.closeModal()}
	></button>

	<div
		role="dialog"
		aria-modal="true"
		aria-labelledby="modal-title"
		tabindex="-1"
		class={`relative z-10 w-full mx-4 bg-cream rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh] ${modal === 'system-prompts' ? 'max-w-5xl' : 'max-w-3xl'}`}
	>
		<div class="flex items-center justify-between px-6 py-4 bg-navy border-b border-navy-light">
			<h2 id="modal-title" class="text-lg font-sans font-semibold text-cream">{titles[modal]}</h2>
			<button
				onclick={() => ui.closeModal()}
				class="text-sand hover:text-cream transition-colors"
				aria-label="Close"
			>
				<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>

		<div class="flex-1 overflow-hidden flex flex-col min-h-0">
			{#if modal === 'system-prompts'}
				<ManageSystemPrompts />
			{:else if modal === 'users'}
				<ManageUsers />
			{:else if modal === 'jobs'}
				<ManageJobs />
			{/if}
		</div>
	</div>
</div>
