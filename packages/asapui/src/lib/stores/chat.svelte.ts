import type { Conversation, ConversationMessage } from '$lib/api/conversations';

// ── Display message types ─────────────────────────────────────────────────────

export interface UserMsg      { kind: 'user';      content: string; }
export interface AssistantMsg { kind: 'assistant'; content: string; streaming?: boolean; }
export interface ThinkingMsg  {
	kind:      'thinking';
	content:   string;
	streaming: boolean;  // true while reasoning tokens are still arriving
	expanded:  boolean;  // user toggle — collapsed by default
}
export interface ToolCallMsg  {
	kind:            'tool_call';
	tool:            string;
	args?:           string;
	result?:         string;
	resultExpanded:  boolean;
	pending:         boolean;
}
export interface ErrorMsg     { kind: 'error';     detail: string; }
export interface RagChunkView {
	id: string; text: string; page_no: number | null;
	heading: string | null; file_name: string; collection: string;
}
export interface RagMsg {
	kind:        'rag';
	chunks:      RagChunkView[];
	searchQuery: string | null;  // the distilled retrieval query, when one ran
	expanded:    boolean;  // collapsed by default — evidence on demand
}

export type DisplayMessage = UserMsg | AssistantMsg | ThinkingMsg | ToolCallMsg | ErrorMsg | RagMsg;

// ── Helpers ───────────────────────────────────────────────────────────────────

function tryParseJson(s: string): unknown {
	try { return JSON.parse(s); } catch { return null; }
}

export function parseApiMessages(msgs: ConversationMessage[]): DisplayMessage[] {
	const result: DisplayMessage[] = [];
	let i = 0;

	while (i < msgs.length) {
		const msg = msgs[i];

		if (msg.role === 'system') { i++; continue; }

		if (msg.role === 'user') {
			result.push({ kind: 'user', content: msg.message });
			// Retrieved document context (RAG) rides on the user message row;
			// render it as a collapsed evidence block right after the question.
			if (msg.retrieved_chunks?.length) {
				result.push({
					kind: 'rag',
					chunks: msg.retrieved_chunks,
					searchQuery: msg.retrieval_query ?? null,
					expanded: false,
				});
			}
			i++; continue;
		}

		if (msg.role === 'assistant') {
			const parsed = tryParseJson(msg.message);
			const asObj = parsed as Record<string, unknown> | null;

			if (asObj && Array.isArray(asObj['tool_calls'])) {
				// Tool call turn — emit reasoning block first if the model reasoned
				// before deciding to call tools (stored in the same DB row).
				if (msg.reasoning) {
					result.push({ kind: 'thinking', content: msg.reasoning, streaming: false, expanded: false });
				}
				// Each tool call pairs with a following tool result
				const toolCalls = asObj['tool_calls'] as Array<{
					function: { name: string; arguments: string };
				}>;
				for (const tc of toolCalls) {
					const block: ToolCallMsg = {
						kind: 'tool_call',
						tool: tc.function.name,
						args: tc.function.arguments,
						resultExpanded: false,
						pending: false,
					};
					result.push(block);
					// Pair with next tool result
					if (i + 1 < msgs.length && msgs[i + 1].role === 'tool') {
						i++;
						const rp = tryParseJson(msgs[i].message) as Record<string, unknown> | null;
						block.result = (rp?.['content'] as string | undefined) ?? msgs[i].message;
					}
				}
				i++; continue;
			}

			// Plain text assistant turn — prepend reasoning block if present
			const text = typeof asObj?.['content'] === 'string' ? asObj['content'] : msg.message;
			if (msg.reasoning) {
				result.push({ kind: 'thinking', content: msg.reasoning, streaming: false, expanded: false });
			}
			if (text) result.push({ kind: 'assistant', content: text });
			i++; continue;
		}

		// Orphan tool message (shouldn't normally happen)
		i++;
	}

	return result;
}

// ── Streaming session ─────────────────────────────────────────────────────────
//
// One in-flight chat turn. All streaming state (live messages, spinner flags)
// lives here rather than on the store, so switching to another conversation
// leaves the stream running invisibly in the background: the view only shows
// session state when the user is looking at the session's conversation, and
// the accumulated messages are waiting when they return.

export interface StreamSession {
	// null until the backend assigns an id (message sent from the New Chat screen)
	conversationId: string | null;
	messages:       DisplayMessage[];
	isThinking:     boolean;   // waiting for first token (spinner)
	isStreaming:    boolean;   // content tokens flowing
	// Aborting the fetch cancels processing server-side too: the backend's
	// agent loop is bound to the SSE connection and stops when it drops.
	abort:          AbortController;
}

// ── Store ─────────────────────────────────────────────────────────────────────

// ── Sampling temperature stops (Precise / Balanced / Exploratory) ────────────
// Applied per message submission and deliberately NOT persisted server-side:
// each send carries whatever the user currently has selected in the UI.
export type TempStop = 'precise' | 'balanced' | 'exploratory';
export const TEMP_STOPS: { key: TempStop; label: string; value: number; desc: string }[] = [
	{ key: 'precise',     label: 'Precise',     value: 0.2, desc: 'Most repeatable. Best for metrics, reports, and queries.' },
	{ key: 'balanced',    label: 'Balanced',    value: 0.5, desc: 'Some variation in phrasing and approach.' },
	{ key: 'exploratory', label: 'Exploratory', value: 0.8, desc: 'More varied and creative. Answers may differ between runs.' },
];
const K_TEMP = 'asap_temp_stop';

class ChatStore {
	conversations    = $state<Conversation[]>([]);
	activeId         = $state<string | null>(null);
	messages         = $state<DisplayMessage[]>([]);   // the VIEWED conversation (at rest)
	session          = $state<StreamSession | null>(null);
	contextPct       = $state<number | null>(null);  // null = unknown / not available

	// Current temperature stop; survives reloads via localStorage.
	tempStop = $state<TempStop>(
		(typeof localStorage !== 'undefined' &&
			(localStorage.getItem(K_TEMP) as TempStop)) || 'precise',
	);
	setTempStop(stop: TempStop) {
		this.tempStop = stop;
		localStorage.setItem(K_TEMP, stop);
	}
	get tempValue(): number {
		return TEMP_STOPS.find(s => s.key === this.tempStop)?.value ?? 0.2;
	}

	get activeConversation() {
		return this.conversations.find(c => c.id === this.activeId) ?? null;
	}

	// True while a chat turn is streaming (in any conversation).
	get busy(): boolean {
		return this.session !== null;
	}

	// True when the user is currently looking at the conversation that has the
	// in-flight stream (including a brand-new chat that has no id yet).
	get viewingSession(): boolean {
		if (!this.session) return false;
		return this.session.conversationId !== null
			? this.session.conversationId === this.activeId
			: this.activeId === null;
	}

	// What the chat panel should render: live session state when viewing the
	// pending conversation, otherwise the resting view.
	get displayMessages(): DisplayMessage[] {
		return this.viewingSession ? this.session!.messages : this.messages;
	}

	get displayThinking(): boolean {
		return this.viewingSession && this.session!.isThinking;
	}

	get displayStreaming(): boolean {
		return this.viewingSession && this.session!.isStreaming;
	}

	async loadConversations() {
		const { conversationsApi } = await import('$lib/api/conversations');
		this.conversations = await conversationsApi.list();
	}

	async selectConversation(id: string) {
		this.activeId = id;
		this.pendingCollections = [];  // only meaningful on the New Chat screen
		// Returning to the conversation with the in-flight stream: adopt the
		// live session buffer instead of fetching — the API would return the
		// turn's already-persisted messages and duplicate the live view.
		if (this.session?.conversationId === id) {
			this.messages   = this.session.messages;
			this.contextPct = this.conversations.find(c => c.id === id)?.context_pct ?? null;
			return;
		}
		this.messages = [];
		const { conversationsApi } = await import('$lib/api/conversations');
		const msgs = await conversationsApi.messages(id);
		// Guard against a stale load racing a faster subsequent selection.
		if (this.activeId !== id) return;
		this.messages = parseApiMessages(msgs);
		// Restore context percentage from the already-loaded conversation list.
		this.contextPct = this.conversations.find(c => c.id === id)?.context_pct ?? null;
	}

	startNewChat() {
		this.activeId   = null;
		this.messages   = [];
		this.contextPct = null;
		this.pendingCollections = [];
	}

	// Collections chosen on the New Chat screen, before any conversation
	// exists. Sent with the first message so the backend attaches them at
	// conversation creation — the first turn's retrieval already sees them.
	pendingCollections = $state<{ id: string; title: string; visibility: string }[]>([]);

	// ── Streaming session lifecycle ──────────────────────────────────────────

	// Begin the in-flight turn: snapshot the viewed conversation and append
	// the user's message. All subsequent stream events mutate the session.
	beginSession(userMessage: string): AbortSignal {
		const abort = new AbortController();
		this.session = {
			conversationId: this.activeId,
			messages: [...this.messages, { kind: 'user', content: userMessage }],
			isThinking: true,
			isStreaming: false,
			abort,
		};
		return abort.signal;
	}

	// User clicked stop: abort the SSE fetch, which also cancels the agent
	// loop server-side. The stream consumer's AbortError handler finalises.
	cancelSession() {
		this.session?.abort.abort();
	}

	// Backend assigned an id to a new conversation. Follow it only if the
	// user is still on the New Chat screen; if they've moved elsewhere, the
	// session keeps streaming in the background under its new id.
	setSessionConversationId(id: string) {
		if (this.session) this.session.conversationId = id;
		if (this.activeId === null) this.activeId = id;
		if (!this.conversations.find(c => c.id === id)) {
			this.loadConversations();
		}
	}

	// Session ended without a normal 'done' (error event or thrown fetch).
	// Keep the live buffer visible if the user is watching; otherwise drop it —
	// everything durable is already persisted server-side.
	failSession(detail: string) {
		if (!this.session) return;
		this.session.isThinking  = false;
		this.session.isStreaming = false;
		this.session.messages = [...this.session.messages, { kind: 'error', detail }];
		if (this.viewingSession) {
			this.messages = this.session.messages;
		}
		this.session = null;
	}

	// Called during streaming to build up the live view
	appendOrUpdateAssistantToken(token: string) {
		if (!this.session) return;
		const last = this.session.messages.at(-1);
		if (last?.kind === 'assistant' && last.streaming) {
			// Mutate in place — Svelte 5 tracks nested property updates
			last.content += token;
		} else {
			this.session.messages = [
				...this.session.messages,
				{ kind: 'assistant', content: token, streaming: true },
			];
		}
	}

	// ── Reasoning / thinking ─────────────────────────────────────────────────

	appendThinkingToken(token: string) {
		if (!this.session) return;
		const last = this.session.messages.at(-1);
		if (last?.kind === 'thinking' && last.streaming) {
			last.content += token;
		} else {
			this.session.messages = [
				...this.session.messages,
				{ kind: 'thinking', content: token, streaming: true, expanded: false },
			];
		}
	}

	finalizeThinking() {
		if (!this.session) return;
		// Seal the last streaming thinking block (called on thinking_done event).
		const msg = [...this.session.messages].reverse().find(
			m => m.kind === 'thinking' && m.streaming,
		) as ThinkingMsg | undefined;
		if (msg) msg.streaming = false;
	}

	// ── Retrieved document context (RAG) ────────────────────────────────────

	addRagContext(chunks: RagChunkView[], searchQuery: string | null = null) {
		if (!this.session) return;
		this.session.messages = [
			...this.session.messages,
			{ kind: 'rag', chunks, searchQuery, expanded: false },
		];
	}

	// ── Tool calls ───────────────────────────────────────────────────────────

	addToolCall(tool: string, args?: string) {
		if (!this.session) return;
		this.session.messages = [
			...this.session.messages,
			{ kind: 'tool_call', tool, args, resultExpanded: false, pending: true },
		];
	}

	resolveToolCall(tool: string, summary: string) {
		if (!this.session) return;
		// Find the last pending tool_call for this tool and update it
		const msg = [...this.session.messages].reverse().find(
			m => m.kind === 'tool_call' && m.tool === tool && m.pending,
		) as ToolCallMsg | undefined;
		if (msg) {
			msg.result  = summary;
			msg.pending = false;
		}
	}

	updateContextPct(pct: number) {
		// The usage event belongs to the session's conversation, which may not
		// be the one on screen.
		const targetId = this.session?.conversationId ?? this.activeId;
		if (this.viewingSession || targetId === this.activeId) this.contextPct = pct;
		const conv = this.conversations.find(c => c.id === targetId);
		if (conv) conv.context_pct = pct;
	}

	async finalizeStream(conversationId: string) {
		const wasViewing = this.viewingSession;
		this.session = null;
		const { conversationsApi } = await import('$lib/api/conversations');
		// Refresh conversation list (title may have been set, message_count updated)
		this.conversations = await conversationsApi.list();
		if (wasViewing && this.activeId === conversationId) {
			// Reload from API for full content (tool results > 200 chars, reasoning, etc.)
			const msgs = await conversationsApi.messages(conversationId);
			if (this.activeId === conversationId) {
				this.messages = parseApiMessages(msgs);
				// Sync context percentage (update_prompt_tokens ran on stop).
				const conv = this.conversations.find(c => c.id === conversationId);
				if (conv?.context_pct != null) this.contextPct = conv.context_pct;
			}
		}
	}

	async deleteConversation(id: string) {
		// Refuse while a response is still streaming into this conversation —
		// the backend agent would keep writing rows into a deleted parent.
		if (this.session?.conversationId === id) return;
		const { conversationsApi } = await import('$lib/api/conversations');
		await conversationsApi.remove(id);
		if (this.activeId === id) {
			this.activeId = null;
			this.messages = [];
		}
		this.conversations = this.conversations.filter(c => c.id !== id);
	}

	async renameConversation(id: string, title: string) {
		const { conversationsApi } = await import('$lib/api/conversations');
		const updated = await conversationsApi.rename(id, title);
		const conv = this.conversations.find(c => c.id === id);
		if (conv) conv.title = updated.title;
	}
}

export const chat = new ChatStore();
