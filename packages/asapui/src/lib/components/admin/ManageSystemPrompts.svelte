<script lang="ts">
	import { systemPromptsApi, type SystemPrompt, type SystemPromptTag } from '$lib/api/systemPrompts';
	import type { Tag } from '$lib/api/tags';

	// ── List state ──────────────────────────────────────────────────────────────
	let showExpired   = $state(false);
	let prompts       = $state<SystemPrompt[]>([]);
	let loadingList   = $state(false);
	let listError     = $state('');
	let openKebab     = $state<string | null>(null);
	let kebabDropUp   = $state(false);

	// Approximate rendered height of the kebab menu (3 items) — used to decide
	// whether to open the menu above the button instead of below.
	const KEBAB_MENU_HEIGHT = 110;

	function toggleKebab(e: MouseEvent, promptId: string) {
		e.stopPropagation();
		if (openKebab === promptId) { openKebab = null; return; }
		const btn  = e.currentTarget as HTMLElement;
		const list = btn.closest('[data-prompt-list]');
		if (list) {
			const btnRect  = btn.getBoundingClientRect();
			const listRect = list.getBoundingClientRect();
			kebabDropUp = btnRect.bottom + KEBAB_MENU_HEIGHT > listRect.bottom;
		} else {
			kebabDropUp = false;
		}
		openKebab = promptId;
	}

	// ── Form state ──────────────────────────────────────────────────────────────
	type PanelMode = 'idle' | 'new' | 'edit';
	let mode          = $state<PanelMode>('idle');
	let selectedId    = $state<string | null>(null);
	let loadingForm   = $state(false);

	let formTitle       = $state('');
	let formDescription = $state('');
	let formText        = $state('');
	let formIsDefault   = $state(false);
	let saving          = $state(false);
	let formError       = $state('');

	// originals for dirty tracking
	let origTitle       = $state('');
	let origDescription = $state('');
	let origText        = $state('');
	let origIsDefault   = $state(false);

	// Tags
	let allTags            = $state<Tag[]>([]);
	let selectedTagIds     = $state<Set<string>>(new Set());
	let existingTagAssocs  = $state<SystemPromptTag[]>([]);
	let addingTag          = $state(false);
	let newTagText         = $state('');

	const tagsDirty = $derived.by(() => {
		const origIds = new Set(existingTagAssocs.map(t => t.tag_id));
		if (selectedTagIds.size !== origIds.size) return true;
		for (const id of selectedTagIds) if (!origIds.has(id)) return true;
		return false;
	});

	const isDirty = $derived(
		formTitle       !== origTitle       ||
		formDescription !== origDescription ||
		formText        !== origText        ||
		formIsDefault   !== origIsDefault   ||
		tagsDirty,
	);

	const filteredPrompts = $derived(
		showExpired ? prompts : prompts.filter(p => !p.expiration_ts),
	);

	// ── Load list ────────────────────────────────────────────────────────────────
	async function loadList() {
		loadingList = true;
		listError   = '';
		try {
			// Always fetch all (including expired) so toggling the checkbox is
			// instant and doesn't require a round-trip.
			prompts = await systemPromptsApi.list(false);
		} catch {
			listError = 'Failed to load system prompts.';
		} finally {
			loadingList = false;
		}
	}

	loadList();

	// ── Form helpers ─────────────────────────────────────────────────────────────
	function clearForm() {
		formTitle       = ''; origTitle       = '';
		formDescription = ''; origDescription = '';
		formText        = ''; origText        = '';
		formIsDefault   = false; origIsDefault = false;
		selectedTagIds  = new Set();
		existingTagAssocs = [];
		allTags           = [];
		addingTag = false; newTagText = '';
		formError = '';
	}

	function confirmIfDirty(): boolean {
		if (!isDirty) return true;
		return confirm('You have unsaved changes. Are you sure you want to discard them?');
	}

	async function loadTags() {
		const { tagsApi } = await import('$lib/api/tags');
		allTags = await tagsApi.list();
	}

	async function openPrompt(id: string) {
		if (selectedId === id && mode === 'edit') return;
		if (!confirmIfDirty()) return;
		loadingForm = true;
		formError   = '';
		mode        = 'edit';
		selectedId  = id;
		try {
			const [prompt, tags] = await Promise.all([
				systemPromptsApi.get(id),
				systemPromptsApi.listTags(id),
				loadTags(),
			]);
			formTitle       = prompt.title;        origTitle       = prompt.title;
			formDescription = prompt.description ?? ''; origDescription = prompt.description ?? '';
			formText        = prompt.prompt_text;  origText        = prompt.prompt_text;
			formIsDefault   = prompt.is_default;   origIsDefault   = prompt.is_default;
			existingTagAssocs = tags;
			selectedTagIds    = new Set(tags.map(t => t.tag_id));
		} catch {
			formError = 'Failed to load system prompt.';
		} finally {
			loadingForm = false;
		}
	}

	async function startNew(prefill?: Partial<Pick<SystemPrompt, 'title' | 'description' | 'prompt_text'>>) {
		if (!confirmIfDirty()) return;
		clearForm();
		mode       = 'new';
		selectedId = null;
		if (prefill) {
			formTitle       = prefill.title       ?? '';
			formDescription = prefill.description ?? '';
			formText        = prefill.prompt_text ?? '';
		}
		await loadTags();
	}

	async function save() {
		if (!formTitle.trim() || !formText.trim()) return;
		saving    = true;
		formError = '';
		try {
			let promptId: string;

			if (mode === 'new') {
				const created = await systemPromptsApi.create({
					title:       formTitle.trim(),
					description: formDescription.trim() || null,
					prompt_text: formText.trim(),
					is_default:  formIsDefault,
				});
				promptId = created.id;
				for (const tagId of selectedTagIds) {
					await systemPromptsApi.addTag(promptId, tagId);
				}
				selectedId = promptId;
				mode = 'edit';
			} else {
				promptId = selectedId!;
				await systemPromptsApi.update(promptId, {
					title:       formTitle.trim(),
					description: formDescription.trim() || null,
					prompt_text: formText.trim(),
					is_default:  formIsDefault,
				});
				// Sync tags
				const existingIds = new Set(existingTagAssocs.map(t => t.tag_id));
				for (const assoc of existingTagAssocs) {
					if (!selectedTagIds.has(assoc.tag_id)) {
						await systemPromptsApi.removeTag(promptId, assoc.tag_id);
					}
				}
				for (const tagId of selectedTagIds) {
					if (!existingIds.has(tagId)) {
						await systemPromptsApi.addTag(promptId, tagId);
					}
				}
			}

			// Update originals so isDirty resets (and the Save button disables)
			formTitle       = formTitle.trim();       origTitle       = formTitle;
			formDescription = formDescription.trim(); origDescription = formDescription;
			formText        = formText.trim();        origText        = formText;
			origIsDefault   = formIsDefault;
			existingTagAssocs = await systemPromptsApi.listTags(promptId);
			selectedTagIds    = new Set(existingTagAssocs.map(t => t.tag_id));

			await loadList();
		} catch {
			formError = 'Failed to save system prompt.';
		} finally {
			saving = false;
		}
	}

	async function cancelEdit() {
		if (!confirmIfDirty()) return;
		if (mode === 'edit' && selectedId) {
			await openPrompt(selectedId);
		} else {
			clearForm();
			mode       = 'idle';
			selectedId = null;
		}
	}

	// ── Kebab actions ────────────────────────────────────────────────────────────
	async function expirePrompt(id: string) {
		openKebab = null;
		if (!confirm('Expire this system prompt? It will no longer be active.')) return;
		try {
			await systemPromptsApi.expire(id);
			if (selectedId === id) { clearForm(); mode = 'idle'; selectedId = null; }
			await loadList();
		} catch (e) {
			// Surfaces the backend guard message when the prompt is assigned to
			// users (admin must unassign via Manage Users first).
			listError = e instanceof Error ? e.message : 'Failed to expire system prompt.';
		}
	}

	async function toggleDefault(prompt: SystemPrompt) {
		openKebab = null;
		try {
			await systemPromptsApi.update(prompt.id, { is_default: !prompt.is_default });
			// If this prompt is open in the editor, sync the form value
			if (selectedId === prompt.id) {
				formIsDefault = !prompt.is_default;
				origIsDefault = !prompt.is_default;
			}
			await loadList();
		} catch {
			listError = 'Failed to update default status.';
		}
	}

	async function duplicatePrompt(prompt: SystemPrompt) {
		openKebab = null;
		await startNew({
			title:       `${prompt.title} (copy)`,
			description: prompt.description ?? '',
			prompt_text: prompt.prompt_text,
		});
	}

	async function unexpirePrompt(id: string) {
		openKebab = null;
		try {
			await systemPromptsApi.unexpire(id);
			await loadList();
		} catch {
			listError = 'Failed to unexpire system prompt.';
		}
	}

	// ── Inline tag creation ──────────────────────────────────────────────────────
	function toggleTag(tagId: string) {
		const next = new Set(selectedTagIds);
		if (next.has(tagId)) next.delete(tagId);
		else                  next.add(tagId);
		selectedTagIds = next;
	}

	async function addNewTag() {
		const name = newTagText.trim();
		if (!name) return;
		try {
			const { tagsApi } = await import('$lib/api/tags');
			const tag = await tagsApi.create(name);
			allTags        = [...allTags, tag];
			selectedTagIds = new Set([...selectedTagIds, tag.id]);
			newTagText     = '';
			addingTag      = false;
		} catch {
			formError = 'Failed to create tag.';
		}
	}

	// Close kebab when clicking outside
	function handleDocClick(e: MouseEvent) {
		const target = e.target as HTMLElement;
		if (!target.closest('[data-kebab]')) openKebab = null;
	}
</script>

<svelte:document onclick={handleDocClick} />

<div class="flex h-[min(600px,80vh)] min-h-0">

	<!-- ── Left panel: list ───────────────────────────────────────────────── -->
	<div class="w-72 shrink-0 border-r border-sand flex flex-col min-h-0">

		<!-- Toolbar -->
		<div class="px-4 py-3 border-b border-sand space-y-2 shrink-0">
			<button
				onclick={() => startNew()}
				class="w-full flex items-center justify-center gap-1.5 px-3 py-2 text-sm bg-navy text-cream rounded-lg hover:bg-navy-light transition-colors"
			>
				<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4" />
				</svg>
				New System Prompt
			</button>
			<label class="flex items-center gap-2 text-xs text-muted cursor-pointer select-none">
				<input type="checkbox" bind:checked={showExpired} class="rounded border-sand accent-navy" />
				Show expired
			</label>
		</div>

		<!-- Column headers -->
		<div class="grid grid-cols-[1fr_auto_auto] items-center px-4 py-1.5 border-b border-sand shrink-0">
			<span class="text-[10px] font-semibold uppercase tracking-wide text-muted-light">Title</span>
			<span class="text-[10px] font-semibold uppercase tracking-wide text-muted-light mr-6">Default</span>
		</div>

		<!-- List error banner (kept above the list so it stays visible) -->
		{#if listError}
			<div class="px-4 py-2 border-b border-red-200 bg-red-50 shrink-0">
				<p class="text-xs text-red-700">{listError}</p>
				<button onclick={() => listError = ''} class="text-[10px] text-red-400 underline">Dismiss</button>
			</div>
		{/if}

		<!-- Prompt list -->
		<div class="flex-1 overflow-y-auto" data-prompt-list>
			{#if loadingList}
				<p class="text-xs text-muted text-center py-6">Loading…</p>
			{:else if filteredPrompts.length === 0}
				<p class="text-xs text-muted-light italic text-center py-6">No system prompts yet.</p>
			{:else}
				{#each filteredPrompts as prompt (prompt.id)}
					{@const expired = !!prompt.expiration_ts}
					<div
						class={[
							'group grid grid-cols-[1fr_auto_auto] items-center px-4 py-2.5 border-b border-sand/60 cursor-pointer transition-colors',
							selectedId === prompt.id
								? 'bg-navy/10'
								: 'hover:bg-parchment',
						].join(' ')}
						onclick={() => !expired && openPrompt(prompt.id)}
						role="button"
						tabindex="0"
						onkeydown={(e) => e.key === 'Enter' && !expired && openPrompt(prompt.id)}
					>
						<!-- Title + expired badge (grayed when expired; kebab stays full-opacity) -->
						<div class={['min-w-0 pr-2', expired ? 'opacity-50' : ''].join(' ')}>
							<p class={['text-sm truncate', expired ? 'line-through text-muted' : 'text-charcoal'].join(' ')}>
								{prompt.title}
							</p>
							{#if expired}
								<span class="text-[10px] text-muted-light">Expired</span>
							{/if}
						</div>

						<!-- Is default -->
						<div class={['mr-4 flex items-center justify-center w-6', expired ? 'opacity-50' : ''].join(' ')}>
							{#if prompt.is_default}
								<svg class="w-3.5 h-3.5 text-gold" fill="currentColor" viewBox="0 0 20 20">
									<path fill-rule="evenodd" d="M16.707 5.293a1 1 0 00-1.414 0L8 12.586l-3.293-3.293a1 1 0 00-1.414 1.414l4 4a1 1 0 001.414 0l8-8a1 1 0 000-1.414z" clip-rule="evenodd"/>
								</svg>
							{:else}
								<span class="text-muted-light text-xs">—</span>
							{/if}
						</div>

						<!-- Kebab -->
						<div class="relative" data-kebab>
							<button
								onclick={(e) => toggleKebab(e, prompt.id)}
								class="p-1 rounded text-muted-light hover:text-charcoal hover:bg-sand transition-colors opacity-0 group-hover:opacity-100 focus:opacity-100"
								aria-label="Actions"
							>
								<svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
									<path d="M10 6a2 2 0 110-4 2 2 0 010 4zm0 6a2 2 0 110-4 2 2 0 010 4zm0 6a2 2 0 110-4 2 2 0 010 4z"/>
								</svg>
							</button>
							{#if openKebab === prompt.id}
								<div
									class={[
										'absolute right-0 z-20 bg-white border border-sand rounded-lg shadow-lg py-1 w-36',
										kebabDropUp ? 'bottom-7' : 'top-7',
									].join(' ')}
									data-kebab
								>
									{#if expired}
										<button
											onclick={() => unexpirePrompt(prompt.id)}
											class="w-full text-left px-3 py-1.5 text-xs hover:bg-parchment transition-colors text-charcoal"
										>
											Unexpire
										</button>
									{:else}
										<button
											onclick={() => toggleDefault(prompt)}
											class="w-full text-left px-3 py-1.5 text-xs hover:bg-parchment transition-colors text-charcoal"
										>
											{prompt.is_default ? 'Clear Default' : 'Make Default'}
										</button>
										<button
											onclick={() => expirePrompt(prompt.id)}
											class="w-full text-left px-3 py-1.5 text-xs hover:bg-parchment transition-colors text-crimson"
										>
											Expire
										</button>
									{/if}
									<button
										onclick={() => duplicatePrompt(prompt)}
										class="w-full text-left px-3 py-1.5 text-xs hover:bg-parchment transition-colors text-charcoal"
									>
										Duplicate
									</button>
								</div>
							{/if}
						</div>
					</div>
				{/each}
			{/if}
		</div>
	</div>

	<!-- ── Right panel: editor ───────────────────────────────────────────────── -->
	<div class="flex-1 flex flex-col min-h-0 min-w-0">

		{#if mode === 'idle'}
			<div class="flex-1 flex flex-col items-center justify-center gap-3 text-center text-muted p-8">
				<svg class="w-10 h-10 text-sand-dark" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
				</svg>
				<p class="text-sm">Select a system prompt to edit, or create a new one.</p>
			</div>

		{:else if loadingForm}
			<div class="flex-1 flex items-center justify-center text-muted text-sm">Loading…</div>

		{:else}
			<div class="flex-1 overflow-y-auto px-6 py-5 flex flex-col gap-5">

				<!-- Title -->
				<div>
					<label class="block text-xs font-semibold uppercase tracking-wide text-muted mb-1.5">
						Title <span class="text-crimson">*</span>
					</label>
					<input
						bind:value={formTitle}
						placeholder="Give this prompt a name"
						class="w-full border border-sand rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-navy/30 focus:border-navy/40"
					/>
				</div>

				<!-- Description -->
				<div>
					<label class="block text-xs font-semibold uppercase tracking-wide text-muted mb-1.5">
						Description
					</label>
					<textarea
						bind:value={formDescription}
						rows={2}
						placeholder="Optional — briefly describe when to use this prompt"
						class="w-full border border-sand rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-navy/30 focus:border-navy/40 resize-y"
					></textarea>
				</div>

				<!-- Prompt text -->
				<div>
					<label class="block text-xs font-semibold uppercase tracking-wide text-muted mb-1.5">
						Prompt text <span class="text-crimson">*</span>
					</label>
					<textarea
						bind:value={formText}
						rows={8}
						placeholder="Enter the system prompt text…"
						class="w-full border border-sand rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-navy/30 focus:border-navy/40 resize-y font-mono"
					></textarea>
				</div>

				<!-- Is Default -->
				<label class="flex items-start gap-2.5 cursor-pointer select-none">
					<input
						type="checkbox"
						bind:checked={formIsDefault}
						class="mt-0.5 rounded border-sand accent-navy"
					/>
					<span class="text-sm text-charcoal">
						Set as default system prompt
						<span class="block text-xs text-muted mt-0.5">
							Only one prompt can be the default. Setting this will clear the current default.
						</span>
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
										selectedTagIds.has(tag.id)
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

				{#if formError}
					<p class="text-xs text-red-600">{formError}</p>
				{/if}
			</div>

			<!-- Footer -->
			<div class="shrink-0 flex items-center justify-end gap-2 px-6 py-4 border-t border-sand">
				<button
					onclick={cancelEdit}
					class="px-4 py-2 text-sm text-muted hover:text-charcoal transition-colors"
				>
					Cancel
				</button>
				<button
					onclick={save}
					disabled={saving || !formTitle.trim() || !formText.trim() || !isDirty}
					class="px-4 py-2 text-sm bg-navy text-cream rounded-lg hover:bg-navy-light disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
				>
					{saving ? 'Saving…' : 'Save'}
				</button>
			</div>
		{/if}
	</div>
</div>
