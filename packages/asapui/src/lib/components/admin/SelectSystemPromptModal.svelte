<script lang="ts">
	import { onMount } from 'svelte';
	import { systemPromptsApi, type SystemPrompt } from '$lib/api/systemPrompts';
	import { tagsApi, type Tag } from '$lib/api/tags';

	const {
		userName,
		currentPromptId,
		onselect,
		oncancel,
	}: {
		userName:        string;
		currentPromptId: string | null;
		onselect:        (promptId: string) => void;
		oncancel:        () => void;
	} = $props();

	let prompts        = $state<SystemPrompt[]>([]);
	let tags           = $state<Tag[]>([]);
	let activeTagIds   = $state<Set<string>>(new Set());
	let selectedId     = $state<string | null>(currentPromptId);
	let loading        = $state(true);
	let error          = $state('');

	onMount(async () => {
		try {
			// Unexpired prompts only — assigning an expired prompt is rejected
			// by the API and would have no effect at chat time anyway.
			[prompts, tags] = await Promise.all([
				systemPromptsApi.list(true),
				tagsApi.list(),
			]);
		} catch {
			error = 'Failed to load system prompts.';
		} finally {
			loading = false;
		}
	});

	const filtered = $derived(
		activeTagIds.size === 0
			? prompts
			: prompts.filter(p => [...activeTagIds].every(id => p.tag_ids.includes(id))),
	);

	const selectedPrompt = $derived(
		prompts.find(p => p.id === selectedId) ?? null,
	);

	function toggleTag(tagId: string) {
		const next = new Set(activeTagIds);
		if (next.has(tagId)) next.delete(tagId);
		else                  next.add(tagId);
		activeTagIds = next;
	}
</script>

<!-- Backdrop (above the AdminModal) -->
<div class="fixed inset-0 z-[70] bg-charcoal/50 flex items-center justify-center p-4">
	<div
		role="dialog"
		aria-modal="true"
		aria-labelledby="select-prompt-title"
		tabindex="-1"
		class="bg-cream rounded-2xl shadow-2xl w-full max-w-4xl h-[min(620px,85vh)] flex flex-col overflow-hidden"
	>
		<!-- Header -->
		<div class="flex items-center justify-between px-6 py-4 bg-navy shrink-0">
			<h2 id="select-prompt-title" class="text-lg font-sans font-semibold text-cream">
				Assign System Prompt — {userName}
			</h2>
			<button
				onclick={oncancel}
				class="text-sand hover:text-cream transition-colors"
				aria-label="Close"
			>
				<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>

		{#if loading}
			<div class="flex-1 flex items-center justify-center text-muted text-sm">Loading…</div>
		{:else if error}
			<div class="flex-1 flex items-center justify-center text-red-600 text-sm">{error}</div>
		{:else}
			<div class="flex-1 flex min-h-0">

				<!-- Left: tag filter + prompt list -->
				<div class="w-72 shrink-0 border-r border-sand flex flex-col min-h-0">
					{#if tags.length > 0}
						<div class="px-4 py-3 border-b border-sand shrink-0">
							<p class="text-[10px] font-semibold uppercase tracking-wide text-muted-light mb-2">Filter by tag</p>
							<div class="flex flex-wrap gap-1.5">
								{#each tags as tag (tag.id)}
									<button
										onclick={() => toggleTag(tag.id)}
										class={[
											'px-2 py-0.5 text-xs rounded-full border transition-colors',
											activeTagIds.has(tag.id)
												? 'bg-navy text-cream border-navy'
												: 'bg-white text-muted border-sand hover:border-navy/40',
										].join(' ')}
									>
										{tag.name}
									</button>
								{/each}
							</div>
						</div>
					{/if}

					<div class="flex-1 overflow-y-auto">
						{#if filtered.length === 0}
							<p class="text-xs text-muted-light italic text-center py-6">
								No system prompts match.
							</p>
						{:else}
							{#each filtered as prompt (prompt.id)}
								<button
									onclick={() => selectedId = prompt.id}
									class={[
										'w-full text-left px-4 py-2.5 border-b border-sand/60 transition-colors',
										selectedId === prompt.id ? 'bg-navy/10' : 'hover:bg-parchment',
									].join(' ')}
								>
									<span class="flex items-center gap-2">
										<span class="text-sm text-charcoal truncate flex-1">{prompt.title}</span>
										{#if prompt.is_default}
											<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-gold/20 text-gold-light font-medium shrink-0">default</span>
										{/if}
										{#if prompt.id === currentPromptId}
											<span class="text-[10px] px-1.5 py-0.5 rounded-full bg-navy/10 text-navy font-medium shrink-0">current</span>
										{/if}
									</span>
								</button>
							{/each}
						{/if}
					</div>
				</div>

				<!-- Right: read-only prompt detail -->
				<div class="flex-1 min-w-0 overflow-y-auto px-6 py-5">
					{#if !selectedPrompt}
						<div class="h-full flex items-center justify-center text-muted text-sm">
							Select a system prompt to review it.
						</div>
					{:else}
						<h3 class="text-base font-semibold text-navy mb-1">{selectedPrompt.title}</h3>
						{#if selectedPrompt.description}
							<p class="text-sm text-muted mb-4">{selectedPrompt.description}</p>
						{:else}
							<p class="text-sm text-muted-light italic mb-4">No description.</p>
						{/if}
						<p class="text-[10px] font-semibold uppercase tracking-wide text-muted-light mb-1.5">Prompt text</p>
						<pre class="text-xs text-charcoal whitespace-pre-wrap break-words font-mono leading-relaxed bg-white border border-sand rounded-lg p-3">{selectedPrompt.prompt_text}</pre>
					{/if}
				</div>
			</div>

			<!-- Footer -->
			<div class="shrink-0 flex items-center justify-end gap-2 px-6 py-4 border-t border-sand bg-cream">
				<button
					onclick={oncancel}
					class="px-4 py-2 text-sm text-muted hover:text-charcoal transition-colors"
				>
					Cancel
				</button>
				<button
					onclick={() => selectedId && onselect(selectedId)}
					disabled={!selectedId || selectedId === currentPromptId}
					class="px-4 py-2 text-sm bg-navy text-cream rounded-lg hover:bg-navy-light disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
				>
					Assign
				</button>
			</div>
		{/if}
	</div>
</div>
