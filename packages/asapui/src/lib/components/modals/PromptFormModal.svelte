<script lang="ts">
	import { untrack } from 'svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import type { Tag } from '$lib/api/tags';
	import type { PromptTag } from '$lib/api/userPrompts';

	let title       = $state('');
	let promptText  = $state('');
	let isGlobal    = $state(false);
	let allTags     = $state<Tag[]>([]);
	let selectedIds = $state<Set<string>>(new Set());
	let existingAssociations = $state<PromptTag[]>([]);
	let newTagText  = $state('');
	let addingTag   = $state(false);
	let loading     = $state(false);
	let saving      = $state(false);
	let error       = $state('');

	const formTitle = $derived(() => {
		if (!ui.promptForm) return '';
		if (ui.promptForm.mode === 'edit') return 'Edit Prompt';
		if (ui.promptForm.mode === 'save') return 'Save Prompt';
		return 'New Prompt';
	});

	$effect(() => {
		const form = ui.promptForm;
		if (!form) return;
		untrack(() => void loadFormData(form));
	});

	async function loadFormData(form: NonNullable<typeof ui.promptForm>) {
		loading = true;
		error   = '';
		try {
			const [{ tagsApi }, { userPromptsApi }] = await Promise.all([
				import('$lib/api/tags'),
				import('$lib/api/userPrompts'),
			]);
			allTags = await tagsApi.list();

			if (form.mode === 'edit') {
				const [prompt, tags] = await Promise.all([
					userPromptsApi.get(form.promptId),
					userPromptsApi.listTags(form.promptId),
				]);
				title                = prompt.title;
				promptText           = prompt.prompt_text;
				isGlobal             = prompt.is_global;
				existingAssociations = tags;
				selectedIds          = new Set(tags.map(t => t.tag_id));
			} else {
				title                = '';
				promptText           = form.mode === 'save' ? form.initialText : '';
				isGlobal             = false;
				existingAssociations = [];
				selectedIds          = new Set();
			}
		} catch {
			error = 'Failed to load form data.';
		} finally {
			loading = false;
		}
	}

	function close() {
		ui.closePromptForm();
		title = ''; promptText = ''; isGlobal = false;
		selectedIds = new Set(); existingAssociations = [];
		newTagText = ''; addingTag = false; error = '';
	}

	async function save() {
		if (!title.trim() || !promptText.trim()) return;
		saving = true;
		error  = '';
		try {
			const { userPromptsApi } = await import('$lib/api/userPrompts');
			const form = ui.promptForm!;
			let promptId: string;

			if (form.mode === 'edit') {
				await userPromptsApi.update(form.promptId, {
					title:       title.trim(),
					prompt_text: promptText.trim(),
					is_global:   isGlobal,
				});
				promptId = form.promptId;

				// Sync tags: remove deselected, add newly selected
				const existingIds = new Set(existingAssociations.map(t => t.tag_id));
				for (const assoc of existingAssociations) {
					if (!selectedIds.has(assoc.tag_id)) {
						await userPromptsApi.removeTag(promptId, assoc.tag_id);
					}
				}
				for (const tagId of selectedIds) {
					if (!existingIds.has(tagId)) {
						await userPromptsApi.addTag(promptId, tagId);
					}
				}
			} else {
				const prompt = await userPromptsApi.create({
					title:       title.trim(),
					prompt_text: promptText.trim(),
					is_global:   isGlobal,
				});
				promptId = prompt.id;
				for (const tagId of selectedIds) {
					await userPromptsApi.addTag(promptId, tagId);
				}
			}

			window.dispatchEvent(new CustomEvent('asap:prompts-updated'));
			close();
		} catch {
			error = 'Failed to save prompt.';
		} finally {
			saving = false;
		}
	}

	async function addNewTag() {
		const name = newTagText.trim();
		if (!name) return;
		try {
			const { tagsApi } = await import('$lib/api/tags');
			const tag = await tagsApi.create(name);
			allTags     = [...allTags, tag];
			selectedIds = new Set([...selectedIds, tag.id]);
			newTagText  = '';
			addingTag   = false;
		} catch {
			error = 'Failed to create tag.';
		}
	}

	function toggleTag(tagId: string) {
		const next = new Set(selectedIds);
		if (next.has(tagId)) next.delete(tagId);
		else                 next.add(tagId);
		selectedIds = next;
	}
</script>

{#if ui.promptForm}
	<!-- Backdrop -->
	<div
		class="fixed inset-0 z-50 bg-charcoal/50 flex items-center justify-center p-4"
		onpointerdown={(e) => { if (e.target === e.currentTarget) close(); }}
	>
		<div
			role="dialog"
			aria-modal="true"
			aria-labelledby="prompt-form-title"
			tabindex="-1"
			class="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[90vh] flex flex-col"
			onpointerdown={(e) => e.stopPropagation()}
		>
			<!-- Header -->
			<div class="flex items-center justify-between px-6 py-4 border-b border-sand shrink-0">
				<h2 id="prompt-form-title" class="font-sans font-semibold text-lg text-navy">
					{formTitle()}
				</h2>
				<button
					onclick={close}
					class="p-1.5 rounded-lg text-muted hover:text-charcoal hover:bg-sand transition-colors"
					aria-label="Close"
				>
					<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>

			{#if loading}
				<div class="flex-1 flex items-center justify-center py-12 text-muted text-sm">
					Loading…
				</div>
			{:else}
				<div class="flex-1 overflow-y-auto px-6 py-5 flex flex-col gap-5">

					<!-- Title -->
					<div>
						<label class="block text-xs font-semibold uppercase tracking-wide text-muted mb-1.5">
							Title <span class="text-crimson">*</span>
						</label>
						<input
							bind:value={title}
							placeholder="Give this prompt a name"
							class="w-full border border-sand rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-navy/30 focus:border-navy/40"
						/>
					</div>

					<!-- Prompt text -->
					<div>
						<label class="block text-xs font-semibold uppercase tracking-wide text-muted mb-1.5">
							Prompt text <span class="text-crimson">*</span>
						</label>
						<textarea
							bind:value={promptText}
							rows={5}
							placeholder="Enter the prompt text…"
							class="w-full border border-sand rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-navy/30 focus:border-navy/40 resize-y"
						></textarea>
					</div>

					<!-- Global flag -->
					<label class="flex items-start gap-2.5 cursor-pointer select-none">
						<input
							type="checkbox"
							bind:checked={isGlobal}
							class="mt-0.5 rounded border-sand accent-navy"
						/>
						<span class="text-sm text-charcoal">
							Make available to all users
							<span class="block text-xs text-muted mt-0.5">When enabled, all users can see and use this prompt.</span>
						</span>
					</label>

					<!-- Tags -->
					<div>
						<div class="flex items-center justify-between mb-2">
							<span class="text-xs font-semibold uppercase tracking-wide text-muted">Tags</span>
							{#if !addingTag}
								<button
									onclick={() => addingTag = true}
									class="flex items-center gap-1 text-xs text-navy hover:text-crimson transition-colors"
								>
									<svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
										<path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4" />
									</svg>
									New tag
								</button>
							{/if}
						</div>

						{#if addingTag}
							<div class="flex gap-2 mb-3">
								<!-- svelte-ignore a11y_autofocus -->
								<input
									bind:value={newTagText}
									placeholder="Tag name"
									autofocus
									onkeydown={(e) => {
										if (e.key === 'Enter')  addNewTag();
										if (e.key === 'Escape') { addingTag = false; newTagText = ''; }
									}}
									class="flex-1 border border-sand rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-navy/30"
								/>
								<button onclick={addNewTag} class="px-3 py-1.5 text-xs bg-navy text-cream rounded-lg hover:bg-navy/80">Add</button>
								<button onclick={() => { addingTag = false; newTagText = ''; }} class="px-3 py-1.5 text-xs text-muted hover:text-charcoal">Cancel</button>
							</div>
						{/if}

						{#if allTags.length === 0}
							<p class="text-xs text-muted-light italic">No tags yet — create one above.</p>
						{:else}
							<div class="flex flex-wrap gap-2">
								{#each allTags as tag (tag.id)}
									<button
										onclick={() => toggleTag(tag.id)}
										class={[
											'px-2.5 py-1 text-xs rounded-full border transition-colors',
											selectedIds.has(tag.id)
												? 'bg-navy text-cream border-navy'
												: 'bg-white text-muted border-sand hover:border-navy/40 hover:text-charcoal',
										].join(' ')}
									>
										{tag.name}
									</button>
								{/each}
							</div>
						{/if}
					</div>

					{#if error}
						<p class="text-xs text-red-600">{error}</p>
					{/if}
				</div>

				<!-- Footer -->
				<div class="flex items-center justify-end gap-2 px-6 py-4 border-t border-sand shrink-0">
					<button onclick={close} class="px-4 py-2 text-sm text-muted hover:text-charcoal transition-colors">
						Cancel
					</button>
					<button
						onclick={save}
						disabled={saving || !title.trim() || !promptText.trim()}
						class="px-4 py-2 text-sm bg-navy text-cream rounded-lg hover:bg-navy/80 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
					>
						{saving ? 'Saving…' : 'Save'}
					</button>
				</div>
			{/if}
		</div>
	</div>
{/if}
