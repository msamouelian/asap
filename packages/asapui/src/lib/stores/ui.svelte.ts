export type AdminModalType = 'system-prompts' | 'users' | 'jobs';
export type LeftTab = 'chats' | 'prompts';
export type PromptFormMode =
	| { mode: 'create' }
	| { mode: 'save'; initialText: string }
	| { mode: 'edit'; promptId: string };

// Left-panel width — user-resizable via the divider, persisted per browser.
const K_LEFT_WIDTH       = 'asap_left_panel_width';
const LEFT_WIDTH_DEFAULT = 288; // matches the old fixed w-72
const LEFT_WIDTH_MIN     = 220;
const LEFT_WIDTH_MAX     = 600;

function storedLeftWidth(): number {
	try {
		const v = parseInt(localStorage.getItem(K_LEFT_WIDTH) ?? '', 10);
		if (v >= LEFT_WIDTH_MIN && v <= LEFT_WIDTH_MAX) return v;
	} catch {
		// localStorage unavailable — fall through to the default.
	}
	return LEFT_WIDTH_DEFAULT;
}

// Left-panel horizontal split: height of the Administrator/User pane above
// the divider. null = "auto" (natural content height) until the user drags.
const K_LEFT_TOP        = 'asap_left_top_height';
export const LEFT_TOP_MIN = 56;   // enough for one section heading + a row

function storedLeftTop(): number | null {
	try {
		const v = parseInt(localStorage.getItem(K_LEFT_TOP) ?? '', 10);
		if (v >= LEFT_TOP_MIN) return v;
	} catch {
		// localStorage unavailable — fall through to auto.
	}
	return null;
}

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

	leftPanelWidth = $state(storedLeftWidth());
	setLeftPanelWidth(px: number) {
		this.leftPanelWidth = Math.min(LEFT_WIDTH_MAX, Math.max(LEFT_WIDTH_MIN, Math.round(px)));
		try { localStorage.setItem(K_LEFT_WIDTH, String(this.leftPanelWidth)); } catch { /* non-fatal */ }
	}
	resetLeftPanelWidth() { this.setLeftPanelWidth(LEFT_WIDTH_DEFAULT); }

	leftTopHeight = $state<number | null>(storedLeftTop());
	setLeftTopHeight(px: number, max: number) {
		this.leftTopHeight = Math.min(max, Math.max(LEFT_TOP_MIN, Math.round(px)));
		try { localStorage.setItem(K_LEFT_TOP, String(this.leftTopHeight)); } catch { /* non-fatal */ }
	}
	resetLeftTopHeight() {
		this.leftTopHeight = null;
		try { localStorage.removeItem(K_LEFT_TOP); } catch { /* non-fatal */ }
	}

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
