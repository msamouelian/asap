export type AdminModalType = 'system-prompts' | 'users' | 'jobs';
export type LeftTab = 'chats' | 'prompts';
export type PromptFormMode =
	| { mode: 'create' }
	| { mode: 'save'; initialText: string }
	| { mode: 'edit'; promptId: string };

class UiStore {
	adminModal  = $state<AdminModalType | null>(null);
	promptForm  = $state<PromptFormMode | null>(null);
	leftTab     = $state<LeftTab>('chats');
	searchQuery = $state('');
	activeTags  = $state<Set<string>>(new Set());

	openModal(modal: AdminModalType)     { this.adminModal = modal; }
	closeModal()                         { this.adminModal = null; }

	documentsOpen = $state(false);
	openDocuments()                      { this.documentsOpen = true; }
	closeDocuments()                     { this.documentsOpen = false; }

	hybridSearchOpen = $state(false);
	openHybridSearch()                   { this.hybridSearchOpen = true; }
	closeHybridSearch()                  { this.hybridSearchOpen = false; }

	// Bumped whenever collections or scopes change (modal toggles, archive,
	// delete, upload completion) so dependents — e.g. the conversation chips
	// row — re-fetch instead of showing stale attachments.
	docsVersion = $state(0);
	bumpDocs()                           { this.docsVersion++; }

	openPromptForm(mode: PromptFormMode) { this.promptForm = mode; }
	closePromptForm()                    { this.promptForm = null; }

	setTab(tab: LeftTab) {
		this.leftTab     = tab;
		this.searchQuery = '';
		this.activeTags  = new Set();
	}

	toggleTag(tagId: string) {
		const next = new Set(this.activeTags);
		if (next.has(tagId)) next.delete(tagId);
		else                 next.add(tagId);
		this.activeTags = next;
	}
}

export const ui = new UiStore();
