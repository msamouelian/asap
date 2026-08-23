<script lang="ts">
	import type { ToolCallMsg } from '$lib/stores/chat.svelte';
	import CypherResultsModal from './CypherResultsModal.svelte';

	const { msg }: { msg: ToolCallMsg } = $props();

	// Pretty-print JSON args
	const formattedArgs = $derived(() => {
		if (!msg.args) return '';
		try {
			return JSON.stringify(JSON.parse(msg.args), null, 2);
		} catch {
			return msg.args;
		}
	});

	// Format the tool name for display
	const displayName = $derived(msg.tool.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()));

	// ── Run-full-query (read-cypher boxes only) ───────────────────────────────
	// Extracts the LLM's query, replaces newlines (real and literal '\n'
	// escapes) with spaces, strips every LIMIT clause, and executes it
	// directly against the database — no LLM involvement.
	const isReadCypher = $derived(msg.tool === 'read-cypher');

	function cleanedQuery(): string | null {
		if (!msg.args) return null;
		let q: string;
		try {
			const parsed = JSON.parse(msg.args) as { query?: unknown };
			if (typeof parsed.query !== 'string') return null;
			q = parsed.query;
		} catch {
			return null;
		}
		return q
			.replace(/\\n/g, ' ')                     // literal '\n' escapes (e.g. '\nRETURN')
			.replace(/[\n\r\t]+/g, ' ')               // real newlines/tabs
			.replace(/\bLIMIT\s+(\d+|\$\w+)\b/gi, ' ') // every LIMIT clause, incl. parameterised
			.replace(/\s{2,}/g, ' ')
			.trim();
	}

	let runQuery = $state<string | null>(null);

	function runFullQuery() {
		runQuery = cleanedQuery();
	}
</script>

<div class="my-2 rounded-lg border border-sand overflow-hidden text-sm font-sans">
	<!-- Header bar -->
	<div class="flex items-center gap-2 px-3 py-2 bg-parchment border-b border-sand">
		<span class="text-base leading-none">🔧</span>
		<span class="font-medium text-navy">{displayName}</span>
		{#if isReadCypher && !msg.pending && cleanedQuery()}
			<button
				onclick={runFullQuery}
				title="Run the full query directly against the database (LIMIT clauses removed) and view all results"
				aria-label="Run full query"
				class="flex items-center gap-1 px-1.5 py-0.5 rounded-md text-xs font-medium text-navy hover:bg-navy hover:text-cream border border-navy/30 transition-colors"
			>
				<svg class="w-3 h-3" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M5 3l14 9-14 9V3z"/>
				</svg>
				Run full
			</button>
		{/if}
		{#if msg.pending}
			<span class="ml-auto flex items-center gap-1 text-xs text-muted-light">
				<span class="inline-block w-1.5 h-1.5 bg-gold rounded-full animate-pulse"></span>
				Running…
			</span>
		{:else if msg.result}
			<span class="ml-auto text-xs text-green-700 font-medium">✓ Done</span>
		{/if}
	</div>

	<!-- Arguments (always visible, collapsed nicely) -->
	{#if formattedArgs()}
		<div class="px-3 py-2 bg-white border-b border-sand">
			<p class="text-[10px] uppercase tracking-wide text-muted-light mb-1">Query</p>
			<pre class="text-xs text-charcoal whitespace-pre-wrap break-words font-mono leading-relaxed overflow-x-auto max-h-40">{formattedArgs()}</pre>
		</div>
	{/if}

	<!-- Result (collapsable) -->
	{#if msg.result}
		<div class="px-3 py-2 bg-white">
			<button
				onclick={() => { msg.resultExpanded = !msg.resultExpanded; }}
				class="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-muted hover:text-navy transition-colors"
			>
				<svg
					class={['w-3 h-3 transition-transform', msg.resultExpanded ? 'rotate-90' : ''].join(' ')}
					fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"
				>
					<path stroke-linecap="round" stroke-linejoin="round" d="M9 18l6-6-6-6" />
				</svg>
				{msg.resultExpanded ? 'Hide result' : 'Show result'}
			</button>
			{#if msg.resultExpanded}
				<pre class="mt-2 text-xs text-charcoal whitespace-pre-wrap break-words font-mono leading-relaxed overflow-x-auto max-h-80 bg-parchment rounded p-2 border border-sand">{msg.result}</pre>
			{/if}
		</div>
	{/if}
</div>

{#if runQuery}
	<CypherResultsModal query={runQuery} onclose={() => (runQuery = null)} />
{/if}
