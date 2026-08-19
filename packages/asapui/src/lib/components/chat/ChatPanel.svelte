<script lang="ts">
	import { onMount } from 'svelte';
	import { auth } from '$lib/stores/auth.svelte';
	import { chat, type StreamSession } from '$lib/stores/chat.svelte';
	import { streamChat } from '$lib/api/chat';
	import AppLogo from '$lib/components/AppLogo.svelte';
	import ThinkingAnimation from '$lib/components/ThinkingAnimation.svelte';
	import MessageBubble from './MessageBubble.svelte';
	import ChatInput from './ChatInput.svelte';
	import ConversationCollections from './ConversationCollections.svelte';
	import type { UserPrompt } from '$lib/api/userPrompts';

	let scrollEl        = $state<HTMLDivElement | null>(null);
	let suggestions     = $state<UserPrompt[]>([]);

	onMount(async () => {
		const { userPromptsApi } = await import('$lib/api/userPrompts');
		const all = await userPromptsApi.list().catch(() => [] as UserPrompt[]);
		const global = all.filter(p => p.is_global);
		suggestions = [...global].sort(() => Math.random() - 0.5).slice(0, 4);
	});

	// Auto-scroll to bottom whenever messages or thinking state changes
	$effect(() => {
		// Track reactive dependencies
		const _ = [chat.displayMessages.length, chat.displayThinking];
		if (scrollEl) {
			scrollEl.scrollTo({ top: scrollEl.scrollHeight, behavior: 'smooth' });
		}
	});

	// A page refresh / tab close aborts the stream and cancels the in-flight
	// turn server-side — warn while a response is still being generated.
	// One permanent listener consulting live state (rather than attach/detach
	// on session changes) so it can never be stale, and skipped for deliberate
	// sign-out navigations (auth.leaving), which confirm separately.
	$effect(() => {
		const warn = (e: BeforeUnloadEvent) => {
			if (chat.busy && !auth.leaving) e.preventDefault();
		};
		window.addEventListener('beforeunload', warn);
		return () => window.removeEventListener('beforeunload', warn);
	});

	async function handleSend(message: string) {
		// One in-flight turn at a time (input is disabled, but guard anyway).
		// Guard via the boolean getter — a direct `if (chat.session) return` would
		// narrow chat.session to null for the whole function (TS keeps property
		// narrowing across function calls), breaking every later read.
		if (chat.busy) return;

		const signal = chat.beginSession(message);
		let conversationId = chat.activeId ?? undefined;
		let firstToken     = true;
		// New Chat: collections picked before the conversation existed ride
		// along with the first message.
		const pendingIds = conversationId ? [] : chat.pendingCollections.map(c => c.id);

		try {
			for await (const event of streamChat(message, conversationId, signal, pendingIds, chat.tempValue)) {
				const sess: StreamSession | null = chat.session;
				if (!sess) break; // session torn down (e.g. failSession)

				if (event.type === 'conversation_id') {
					conversationId = event.conversation_id;
					chat.setSessionConversationId(event.conversation_id);
					chat.pendingCollections = []; // persisted server-side now
				} else if (event.type === 'rag_context') {
					chat.addRagContext(event.chunks, event.search_query);
				} else if (event.type === 'thinking_token') {
					// First reasoning chunk — hide the waiting spinner; reasoning
					// block shows its own streaming indicator.
					sess.isThinking = false;
					chat.appendThinkingToken(event.content);
				} else if (event.type === 'thinking_done') {
					chat.finalizeThinking();
				} else if (event.type === 'token') {
					if (firstToken) {
						sess.isThinking  = false;
						sess.isStreaming = true;
						firstToken = false;
					}
					chat.appendOrUpdateAssistantToken(event.content);
				} else if (event.type === 'tool_start') {
					sess.isThinking  = false;
					sess.isStreaming = false;
					chat.addToolCall(event.tool, event.args);
				} else if (event.type === 'tool_result') {
					chat.resolveToolCall(event.tool, event.summary);
					// LLM will continue — set thinking again until next token
					sess.isThinking = true;
				} else if (event.type === 'usage') {
					chat.updateContextPct(event.context_pct);
				} else if (event.type === 'done') {
					if (conversationId) {
						await chat.finalizeStream(conversationId);
					}
					break;
				} else if (event.type === 'error') {
					chat.failSession(event.detail);
					break;
				}
			}
		} catch (err) {
			if (err instanceof DOMException && err.name === 'AbortError') {
				// User clicked stop. The turn's completed rounds are already
				// persisted server-side; reload them and drop the live buffer.
				if (conversationId) {
					await chat.finalizeStream(conversationId);
				} else {
					chat.session = null;
					chat.loadConversations();
				}
				return;
			}
			chat.failSession(err instanceof Error ? err.message : 'Unexpected error');
		}
	}

	const firstName = $derived(() => {
		return auth.displayName || 'there';
	});
</script>

<div class="flex flex-col h-full bg-cream">

	<!-- ── Message area ──────────────────────────────────────────────── -->
	<div bind:this={scrollEl} class="flex-1 overflow-y-auto px-6 py-6 min-h-0">

		{#if chat.displayMessages.length === 0 && !chat.displayThinking}
			<!-- Welcome / empty state -->
			<div class="flex flex-col items-center justify-center h-full gap-6 select-none">
				<AppLogo size={80} />
				<div class="text-center">
					<h2 class="text-3xl font-sans font-semibold text-navy mb-1">
						Hello, {firstName()}
					</h2>
					<p class="text-muted text-sm">
						Get answers from your archival data, fast.
					</p>
				</div>

				<!-- Suggested questions -->
				{#if suggestions.length > 0}
					<div class="grid grid-cols-1 sm:grid-cols-2 gap-2 max-w-lg w-full mt-2">
						{#each suggestions as suggestion (suggestion.id)}
							<button
								onclick={() => handleSend(suggestion.prompt_text)}
								class="text-left text-sm px-4 py-3 rounded-xl border border-sand bg-white hover:border-navy/40 hover:bg-parchment transition-colors text-muted hover:text-navy leading-snug"
							>
								{suggestion.title}
							</button>
						{/each}
					</div>
				{/if}
			</div>

		{:else}
			<div class="max-w-3xl mx-auto">
				{#each chat.displayMessages as msg, i (i)}
					<MessageBubble {msg} />
				{/each}

				{#if chat.displayThinking}
					<div class="flex items-start gap-3 mb-4">
						<div class="w-8 h-8 shrink-0 rounded-full bg-parchment border border-sand flex items-center justify-center text-base mt-0.5">
							📦
						</div>
						<ThinkingAnimation />
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<!-- ── Context usage pill ───────────────────────────────────────── -->
	{#if chat.contextPct !== null}
		<div class="px-4 py-1 flex justify-end bg-cream border-t border-sand/30">
			<span
				title="Portion of the model's context window used by this conversation"
				class={[
					'text-xs px-2 py-0.5 rounded-full font-medium tabular-nums cursor-default',
					chat.contextPct >= 75 ? 'bg-red-50 text-red-600' :
					chat.contextPct >= 50 ? 'bg-amber-50 text-amber-700' :
					'bg-navy/10 text-navy',
				].join(' ')}>
				{chat.contextPct}% context
			</span>
		</div>
	{/if}

	<!-- ── Document collections in effect (both scopes) ─────────────── -->
	<ConversationCollections />

	<!-- ── Input ─────────────────────────────────────────────────────── -->
	{#if chat.busy && !chat.viewingSession}
		<div class="px-6 pb-1 max-w-3xl mx-auto w-full">
			<p class="text-xs text-muted flex items-center gap-1.5">
				<span class="inline-block w-1.5 h-1.5 rounded-full bg-navy animate-pulse"></span>
				A response is still being generated in another conversation — you can
				send a new message when it finishes.
			</p>
		</div>
	{/if}
	<ChatInput
		onsubmit={handleSend}
		disabled={chat.busy}
		streaming={chat.busy && chat.viewingSession}
		oncancel={() => chat.cancelSession()}
	/>
</div>
