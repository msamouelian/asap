<script lang="ts">
	import { onMount } from 'svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import TagPill from '$lib/components/TagPill.svelte';
	import type { UserPrompt } from '$lib/api/userPrompts';
	import type { Tag } from '$lib/api/tags';

	const { searchQuery }: { searchQuery: string } = $props();

	let prompts    = $state<UserPrompt[]>([]);
	let tags       = $state<Tag[]>([]);
	let promptTags = $state<Map<string, string[]>>(new Map());
	let showGlobal = $state(true);

	// Kebab menu state
	let menuOpen = $state<string | null>(null);
	let menuPos  = $state<{ top: number; right: number } | null>(null);

	async function loadData() {
		const [{ userPromptsApi }, { tagsApi }] = await Promise.all([
			import('$lib/api/userPrompts'),
			import('$lib/api/tags'),
		]);
		[prompts, tags] = await Promise.all([userPromptsApi.list(), tagsApi.list()]);
		const entries = await Promise.all(
			prompts.map(async p => {
				try {
					const pt = await userPromptsApi.listTags(p.id);
					return [p.id, pt.map(t => t.tag_id)] as [string, string[]];
				} catch {
					return [p.id, []] as [string, string[]];
				}
			}),
		);
		promptTags = new Map(entries);
	}

	onMount(() => {
		loadData();
		window.addEventListener('asap:prompts-updated', loadData);
		return () => window.removeEventListener('asap:prompts-updated', loadData);
	});

	const filtered = $derived(() => {
		let list = prompts;

		// When showGlobal is false, hide global prompts regardless of who created them
		if (!showGlobal) {
			list = list.filter(p => !p.is_global);
		}

		// Active tag filter
		if (ui.activeTags.size > 0) {
			list = list.filter(p => {
				const pt = promptTags.get(p.id) ?? [];
				return [...ui.activeTags].every(tid => pt.includes(tid));
			});
		}

		// Text search
		if (searchQuery) {
			const q = searchQuery.toLowerCase();
			list = list.filter(p => p.title.toLowerCase().includes(q));
		}

		return [...list].sort((a, b) => a.title.localeCompare(b.title));
	});

	function loadPrompt(p: UserPrompt) {
		window.dispatchEvent(new CustomEvent('asap:insert-prompt', { detail: p.prompt_text }));
	}

	function openMenu(e: MouseEvent, id: string) {
		e.stopPropagation();
		if (menuOpen === id) { closeMenu(); return; }
		const btn = e.currentTarget as HTMLElement;
		const rect = btn.getBoundingClientRect();
		const DROPDOWN_H = 120;
		const top = rect.bottom + DROPDOWN_H > window.innerHeight
			? rect.top - DROPDOWN_H - 4
			: rect.bottom + 4;
		menuPos = { top, right: window.innerWidth - rect.right };
		menuOpen = id;
	}

	function closeMenu() {
		menuOpen = null;
		menuPos  = null;
	}

	async function deletePrompt(id: string) {
		closeMenu();
		const { userPromptsApi } = await import('$lib/api/userPrompts');
		await userPromptsApi.remove(id);
		prompts = prompts.filter(p => p.id !== id);
	}
</script>

<!-- Backdrop + fixed dropdown -->
{#if menuOpen && menuPos}
	<div class="fixed inset-0 z-40" onpointerdown={() => closeMenu()}></div>
	<div
		class="fixed z-50 bg-white border border-sand rounded-lg shadow-lg py-1 w-40"
		style="top: {menuPos.top}px; right: {menuPos.right}px"
		onpointerdown={(e) => e.stopPropagation()}
	>
		<button
			onclick={() => {
				const p = prompts.find(x => x.id === menuOpen!);
				if (p) loadPrompt(p);
				closeMenu();
			}}
			class="w-full flex items-center gap-2 px-3 py-2 text-sm text-charcoal hover:bg-parchment text-left"
		>
			<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M5 12h14M12 5l7 7-7 7" />
			</svg>
			Load prompt
		</button>
		<button
			onclick={() => { ui.openPromptForm({ mode: 'edit', promptId: menuOpen! }); closeMenu(); }}
			class="w-full flex items-center gap-2 px-3 py-2 text-sm text-charcoal hover:bg-parchment text-left"
		>
			<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M15.232 5.232l3.536 3.536M9 13l6.768-6.768a2 2 0 0 1 2.829 2.829L11.829 15.83 8 17l1.171-3.829z" />
			</svg>
			Edit prompt
		</button>
		<button
			onclick={() => deletePrompt(menuOpen!)}
			class="w-full flex items-center gap-2 px-3 py-2 text-sm text-red-600 hover:bg-red-50 text-left"
		>
			<svg class="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
				<path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0 1 16.138 21H7.862a2 2 0 0 1-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v3M4 7h16" />
			</svg>
			<span class="font-medium">Delete</span>
		</button>
	</div>
{/if}

<!-- Show global checkbox -->
<div class="px-4 pb-1 shrink-0">
	<label class="flex items-center gap-2 cursor-pointer select-none">
		<input
			type="checkbox"
			bind:checked={showGlobal}
			class="rounded border-sand accent-navy"
		/>
		<span class="text-xs text-muted">Show global prompts</span>
	</label>
</div>

<!-- Tag filter bar -->
{#if tags.length > 0}
	<div class="px-3 pb-2 shrink-0">
		<div class="flex gap-1.5 overflow-x-auto pb-1 scrollbar-hide">
			{#each tags as tag (tag.id)}
				<TagPill
					name={tag.name}
					active={ui.activeTags.has(tag.id)}
					onclick={() => ui.toggleTag(tag.id)}
				/>
			{/each}
		</div>
	</div>
{/if}

<!-- Prompt list -->
<div class="flex flex-col gap-0 overflow-y-auto flex-1 min-h-0 px-2 pb-2">
	{#if filtered().length === 0}
		<p class="text-xs text-muted-light text-center py-6 italic">
			{searchQuery || ui.activeTags.size > 0 ? 'No matching prompts' : 'No prompts yet'}
		</p>
	{:else}
		<!-- Header row -->
		<div class="grid grid-cols-[1fr_auto] px-3 py-1.5 text-xs font-semibold text-muted uppercase tracking-wide border-b border-sand mb-1">
			<span>Title</span>
			<span class="w-12 text-center">Global</span>
		</div>

		{#each filtered() as prompt (prompt.id)}
			<div class="relative group/prompt">
				<div
					class="grid grid-cols-[1fr_auto] w-full items-center pl-3 pr-8 py-2.5 rounded-lg text-sm text-left"
					title={prompt.prompt_text}
				>
					<span class="truncate text-charcoal font-medium leading-snug">
						{prompt.title}
					</span>
					<span class="w-12 flex justify-center shrink-0">
						{#if prompt.is_global}
							<svg class="w-4 h-4 text-gold" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7" />
							</svg>
						{/if}
					</span>
				</div>

				<!-- 3-dot kebab -->
				<button
					onclick={(e) => openMenu(e, prompt.id)}
					class={[
						'absolute right-1 top-1/2 -translate-y-1/2 p-1.5 rounded-md transition-opacity',
						menuOpen === prompt.id
							? 'opacity-100'
							: 'opacity-0 group-hover/prompt:opacity-100',
						'text-muted-light hover:text-charcoal hover:bg-sand',
					].join(' ')}
					aria-label="Prompt options"
				>
					<svg class="w-3 h-3" fill="currentColor" viewBox="0 0 24 24">
						<circle cx="12" cy="5"  r="1.5"/>
						<circle cx="12" cy="12" r="1.5"/>
						<circle cx="12" cy="19" r="1.5"/>
					</svg>
				</button>
			</div>
		{/each}
	{/if}
</div>
