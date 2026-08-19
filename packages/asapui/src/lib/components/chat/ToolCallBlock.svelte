<script lang="ts">
	import type { ToolCallMsg } from '$lib/stores/chat.svelte';

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
</script>

<div class="my-2 rounded-lg border border-sand overflow-hidden text-sm font-sans">
	<!-- Header bar -->
	<div class="flex items-center gap-2 px-3 py-2 bg-parchment border-b border-sand">
		<span class="text-base leading-none">🔧</span>
		<span class="font-medium text-navy">{displayName}</span>
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
