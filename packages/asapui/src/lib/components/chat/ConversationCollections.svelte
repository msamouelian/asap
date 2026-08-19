<script lang="ts">
	import { chat } from '$lib/stores/chat.svelte';
	import { ui } from '$lib/stores/ui.svelte';
	import {
		documentsApi,
		type CollectionInfo,
		type ScopedCollection,
	} from '$lib/api/documents';

	let effective  = $state<ScopedCollection[]>([]);
	let available  = $state<CollectionInfo[]>([]);
	let pickerOpen = $state(false);
	let loadedFor  = $state<string | undefined>(undefined);

	// Reload when the viewed conversation changes OR when collections/scopes
	// change elsewhere (ui.docsVersion is bumped by the Documents modal), so
	// e.g. unchecking an account-level collection immediately updates chips.
	$effect(() => {
		const key = `${chat.activeId ?? 'new'}|${ui.docsVersion}`;
		if (key === loadedFor) return;
		loadedFor = key;
		pickerOpen = false;
		(async () => {
			try {
				effective = chat.activeId
					? await documentsApi.effectiveScope(chat.activeId)
					: (await documentsApi.userScope());
			} catch {
				effective = [];
			}
		})();
	});

	// Chips to render: server-side effective scopes, plus (on New Chat) the
	// locally buffered pending attachments.
	const chips = $derived<ScopedCollection[]>(
		chat.activeId
			? effective
			: [
				...effective,
				...chat.pendingCollections.map(c => ({
					id: c.id, title: c.title,
					visibility: c.visibility as 'private' | 'shared',
					scope: 'conversation' as const,
				})),
			],
	);

	async function openPicker() {
		try {
			const colls = await documentsApi.list();
			const inEffect = new Set(chips.map(e => e.id));
			available = colls.filter(
				c => c.status === 'ready' && !c.archived && !inEffect.has(c.id),
			);
			pickerOpen = true;
		} catch { /* listing failed — keep picker closed */ }
	}

	async function attach(c: CollectionInfo) {
		pickerOpen = false;
		if (!chat.activeId) {
			// No conversation yet: buffer locally; sent with the first message.
			chat.pendingCollections = [...chat.pendingCollections,
				{ id: c.id, title: c.title, visibility: c.visibility }];
			return;
		}
		await documentsApi.enableConversationScope(chat.activeId, c.id).catch(() => {});
		effective = await documentsApi.effectiveScope(chat.activeId).catch(() => effective);
	}

	async function detach(s: ScopedCollection) {
		if (s.scope !== 'conversation') return;
		if (!chat.activeId) {
			chat.pendingCollections = chat.pendingCollections.filter(c => c.id !== s.id);
			return;
		}
		await documentsApi.disableConversationScope(chat.activeId, s.id).catch(() => {});
		effective = await documentsApi.effectiveScope(chat.activeId).catch(() => effective);
	}
</script>

<!-- Always rendered: even with no chips, + Add offers attachment. -->
<div class="px-6 pb-1 max-w-3xl mx-auto w-full flex items-center gap-1.5 flex-wrap">
		<span class="text-[11px] text-muted-light shrink-0" title="Document collections whose content can support answers in this conversation">
			📚 Collections:
		</span>

		{#each chips as s (s.id)}
			<span
				class={['inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full border',
					s.scope === 'user'
						? 'bg-sand/60 border-sand text-charcoal'
						: 'bg-navy/10 border-navy/20 text-navy'].join(' ')}
				title={s.scope === 'user'
					? 'Enabled for all your conversations (manage in Document Collections)'
					: 'Enabled for this conversation only'}
			>
				{s.scope === 'user' ? '👤' : '💬'} {s.title}
				{#if s.scope === 'conversation'}
					<button onclick={() => detach(s)} aria-label={`Remove ${s.title} from this conversation`}
						class="hover:text-red-600 leading-none">×</button>
				{/if}
			</span>
		{/each}

		<div class="relative">
				<button onclick={openPicker}
					class="text-[11px] px-2 py-0.5 rounded-full border border-dashed border-navy/40 text-navy hover:bg-navy hover:text-cream transition-colors"
					title="Add a document collection to this conversation">
					+ Add
				</button>
				{#if pickerOpen}
					<div class="fixed inset-0 z-40" role="presentation" onpointerdown={() => (pickerOpen = false)}></div>
					<div class="absolute bottom-6 left-0 z-50 w-72 max-h-56 overflow-y-auto bg-white border border-sand rounded-lg shadow-lg py-1">
						{#if available.length === 0}
							<p class="px-3 py-2 text-xs text-muted italic">
								No further collections available.
								<button class="underline" onclick={() => { pickerOpen = false; ui.openDocuments(); }}>
									Manage collections
								</button>
							</p>
						{:else}
							{#each available as c (c.id)}
								<button onclick={() => attach(c)}
									class="w-full text-left px-3 py-2 text-xs text-charcoal hover:bg-parchment">
									<span class="font-medium">{c.title}</span>
									<span class="text-muted-light"> · {c.visibility}</span>
								</button>
							{/each}
						{/if}
					</div>
				{/if}
			</div>
	</div>
