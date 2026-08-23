<script lang="ts">
	// Recursive collapsible tree for structured Cypher result values (nodes,
	// maps, lists). Collapsed by default at every level. Recursion via
	// self-import (svelte:self is deprecated in Svelte 5).
	import JsonTree from './JsonTree.svelte';

	let { value, label = null }: { value: unknown; label?: string | null } = $props();

	let expanded = $state(false);

	const isObj  = $derived(value !== null && typeof value === 'object' && !Array.isArray(value));
	const isArr  = $derived(Array.isArray(value));
	const entries = $derived(
		isArr
			? (value as unknown[]).map((v, i) => [String(i), v] as [string, unknown])
			: isObj
				? Object.entries(value as Record<string, unknown>)
				: [],
	);

	function summary(): string {
		if (isArr) {
			const n = (value as unknown[]).length;
			return `[…] ${n} item${n === 1 ? '' : 's'}`;
		}
		const n = entries.length;
		return `{…} ${n} key${n === 1 ? '' : 's'}`;
	}

	function fmtScalar(v: unknown): string {
		if (v === null || v === undefined) return 'null';
		if (typeof v === 'string') return v;
		return JSON.stringify(v);
	}
</script>

{#if isObj || isArr}
	<div class="font-mono text-xs leading-relaxed">
		<button
			onclick={() => (expanded = !expanded)}
			class="inline-flex items-center gap-1 text-navy hover:text-navy-light"
		>
			<svg
				class={['w-2.5 h-2.5 shrink-0 transition-transform', expanded ? 'rotate-90' : ''].join(' ')}
				fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24"
			>
				<path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7" />
			</svg>
			{#if label}<span class="text-charcoal">{label}:</span>{/if}
			<span class="text-muted">{summary()}</span>
		</button>
		{#if expanded}
			<div class="pl-4 border-l border-sand ml-1 mt-0.5 flex flex-col gap-0.5">
				{#each entries as [k, v] (k)}
					{#if v !== null && typeof v === 'object'}
						<JsonTree value={v} label={k} />
					{:else}
						<div class="break-words">
							<span class="text-charcoal">{k}:</span>
							<span class="text-muted">{fmtScalar(v)}</span>
						</div>
					{/if}
				{/each}
			</div>
		{/if}
	</div>
{:else}
	<span class="font-mono text-xs text-muted break-words">{fmtScalar(value)}</span>
{/if}
