<script lang="ts">
	import { onMount } from 'svelte';
	import type { CypherRunResult } from '$lib/api/cypher';
	import { cypherApi } from '$lib/api/cypher';
	import JsonTree from './JsonTree.svelte';

	// query arrives already cleaned (newlines → spaces, LIMIT clauses removed).
	const { query, onclose }: { query: string; onclose: () => void } = $props();

	let result   = $state<CypherRunResult | null>(null);
	let errorMsg = $state('');
	let loading  = $state(true);
	let queryExpanded = $state(false);

	onMount(async () => {
		try {
			result = await cypherApi.run(query);
		} catch (e) {
			errorMsg = e instanceof Error ? e.message : 'Query failed.';
		} finally {
			loading = false;
		}
	});

	function isStructured(v: unknown): boolean {
		return v !== null && typeof v === 'object';
	}

	function fmtCell(v: unknown): string {
		if (v === null || v === undefined) return '';
		if (typeof v === 'string') return v;
		return JSON.stringify(v);
	}

	// ── CSV export ────────────────────────────────────────────────────────────
	// Structured values (nodes, maps, lists) are serialized as JSON inside
	// their cell so every result "flattens": one row per record, one column
	// per returned variable.
	function csvEscape(s: string): string {
		return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
	}

	function downloadCsv() {
		if (!result) return;
		const lines = [
			result.columns.map(csvEscape).join(','),
			...result.rows.map(row => row.map(v => csvEscape(fmtCell(v))).join(',')),
		];
		// BOM so Excel opens UTF-8 correctly.
		const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `cypher-results-${new Date().toISOString().slice(0, 19).replace(/[T:]/g, '-')}.csv`;
		a.click();
		URL.revokeObjectURL(url);
	}
</script>

<div class="fixed inset-0 z-50 flex items-center justify-center bg-charcoal/50 backdrop-blur-sm">
	<button type="button" class="absolute inset-0 w-full h-full cursor-default"
		aria-label="Close modal" onclick={onclose}></button>

	<div role="dialog" aria-modal="true" aria-labelledby="cypher-results-title" tabindex="-1"
		class="relative z-10 w-full mx-4 bg-cream rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh] max-w-5xl"
	>
		<!-- Header -->
		<div class="px-5 pt-4 pb-3 border-b border-sand flex items-start gap-3">
			<div class="flex-1 min-w-0">
				<h2 id="cypher-results-title" class="text-base font-semibold text-charcoal">Query results</h2>
				<button
					onclick={() => (queryExpanded = !queryExpanded)}
					class="text-xs text-muted hover:text-navy transition-colors mt-0.5"
				>
					{queryExpanded ? 'Hide query' : 'Show query'}
				</button>
				{#if queryExpanded}
					<pre class="mt-1 text-xs text-charcoal font-mono whitespace-pre-wrap break-words bg-parchment border border-sand rounded p-2 max-h-28 overflow-y-auto">{query}</pre>
				{/if}
			</div>
			<div class="flex items-center gap-2 shrink-0">
				{#if result && result.rows.length > 0}
					<button
						onclick={downloadCsv}
						class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-navy text-cream hover:bg-navy-light transition-colors"
					>
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
							<path stroke-linecap="round" stroke-linejoin="round" d="M12 4v12m0 0l-4-4m4 4l4-4M4 20h16" />
						</svg>
						Download CSV
					</button>
				{/if}
				<button
					onclick={onclose}
					aria-label="Close"
					class="p-1.5 rounded-lg text-muted hover:text-charcoal hover:bg-sand transition-colors"
				>
					<svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
					</svg>
				</button>
			</div>
		</div>

		<!-- Status line -->
		{#if result}
			<div class="px-5 py-2 border-b border-sand flex items-center gap-3 text-xs text-muted shrink-0">
				<span class="tabular-nums">{result.row_count.toLocaleString()} row{result.row_count === 1 ? '' : 's'}</span>
				{#if result.truncated}
					<span class="px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 font-medium">
						Truncated at {result.row_count.toLocaleString()} rows (server safety ceiling)
					</span>
				{/if}
			</div>
		{/if}

		<!-- Body -->
		<div class="flex-1 min-h-0 overflow-auto">
			{#if loading}
				<div class="flex items-center justify-center gap-2 py-16 text-muted text-sm">
					<svg class="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
						<circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
						<path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
					</svg>
					Running query…
				</div>
			{:else if errorMsg}
				<div class="m-5 bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-700 font-mono whitespace-pre-wrap break-words">
					{errorMsg}
				</div>
			{:else if result && result.rows.length === 0}
				<p class="text-sm text-muted-light text-center py-16 italic">The query returned no rows.</p>
			{:else if result}
				<table class="w-full text-sm border-collapse">
					<thead class="sticky top-0 bg-parchment z-10">
						<tr>
							{#each result.columns as col (col)}
								<th class="text-left font-semibold text-charcoal px-3 py-2 border-b border-sand whitespace-nowrap">
									{col}
								</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each result.rows as row, i (i)}
							<tr class="border-b border-sand/50 hover:bg-parchment/60 align-top">
								{#each row as cell, j (j)}
									<td class="px-3 py-1.5 text-charcoal max-w-md">
										{#if isStructured(cell)}
											<JsonTree value={cell} />
										{:else}
											<span class="break-words">{fmtCell(cell)}</span>
										{/if}
									</td>
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</div>
	</div>
</div>
