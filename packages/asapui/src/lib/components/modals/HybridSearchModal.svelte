<script lang="ts">
	import { ui } from '$lib/stores/ui.svelte';
	import { searchApi, type HybridResult } from '$lib/api/search';

	let topic     = $state('');
	let limit     = $state(500);
	let loading   = $state(false);
	let errorMsg  = $state<string | null>(null);
	let results   = $state<HybridResult[]>([]);
	let searched  = $state<string | null>(null); // topic of the displayed results

	// ── Filter (applies to title, in-collection, and evidence columns) ────
	let filter = $state('');
	const filtered = $derived.by(() => {
		const q = filter.trim().toLowerCase();
		if (!q) return results;
		return results.filter(r =>
			(r.title ?? '').toLowerCase().includes(q) ||
			(r.in_collection ?? '').toLowerCase().includes(q) ||
			(r.excerpt ?? '').toLowerCase().includes(q),
		);
	});

	// ── Sorting ───────────────────────────────────────────────────────────
	type SortKey = 'rank' | 'title' | 'record_type' | 'score' | 'match_source' | 'in_collection';
	let sortKey = $state<SortKey>('rank');
	let sortAsc = $state(true);

	function setSort(key: SortKey) {
		if (sortKey === key) sortAsc = !sortAsc;
		else { sortKey = key; sortAsc = key === 'rank'; }
		page = 1;
	}

	const sorted = $derived.by(() => {
		const rows = [...filtered];
		rows.sort((a, b) => {
			const av = a[sortKey], bv = b[sortKey];
			if (av == null && bv == null) return 0;
			if (av == null) return 1;
			if (bv == null) return -1;
			const cmp = typeof av === 'number' && typeof bv === 'number'
				? av - bv
				: String(av).localeCompare(String(bv));
			return sortAsc ? cmp : -cmp;
		});
		return rows;
	});

	// ── Paging ────────────────────────────────────────────────────────────
	let pageSize = $state(25);
	let page     = $state(1);
	const pageCount = $derived(Math.max(1, Math.ceil(sorted.length / pageSize)));
	const pageRows  = $derived(sorted.slice((page - 1) * pageSize, page * pageSize));

	async function search() {
		const t = topic.trim();
		if (!t || loading) return;
		loading = true; errorMsg = null;
		try {
			const res = await searchApi.hybrid(t, limit);
			results = res.results;
			searched = res.topic;
			page = 1; sortKey = 'rank'; sortAsc = true; filter = '';
		} catch (e) {
			errorMsg = e instanceof Error ? e.message : 'Search failed.';
		} finally {
			loading = false;
		}
	}

	function exportCsv() {
		const cols = ['rank', 'title', 'record_type', 'score', 'match_source',
		              'in_collection', 'excerpt', 'aspace_url', 'in_collection_url'] as const;
		const cell = (v: unknown): string => {
			let s = v == null ? '' : String(v).replace(/\s+/g, ' ').trim();
			if (/^[=+\-@]/.test(s)) s = "'" + s;      // spreadsheet formula-injection guard
			if (/[",\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
			return s;
		};
		const lines = [cols.join(','), ...sorted.map(r => cols.map(c => cell(r[c])).join(','))];
		// BOM so Excel opens UTF-8 correctly (accented names in archival data).
		const blob = new Blob(['\uFEFF' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });
		const url  = URL.createObjectURL(blob);
		const a    = document.createElement('a');
		a.href     = url;
		const slug = (searched ?? 'search').toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 40);
		a.download = `asap-hybrid-${slug}-${new Date().toISOString().slice(0, 10)}.csv`;
		a.click();
		URL.revokeObjectURL(url);
	}

	function arrow(key: SortKey): string {
		return sortKey === key ? (sortAsc ? ' ▲' : ' ▼') : '';
	}

	const COLS: { key: SortKey; label: string }[] = [
		{ key: 'rank',          label: 'Rank' },
		{ key: 'title',         label: 'Title' },
		{ key: 'record_type',   label: 'Type' },
		{ key: 'score',         label: 'Score' },
		{ key: 'match_source',  label: 'Match source' },
		{ key: 'in_collection', label: 'In collection' },
	];
</script>

<div class="fixed inset-0 z-50 flex items-center justify-center bg-charcoal/50 backdrop-blur-sm">
	<button type="button" class="absolute inset-0 w-full h-full cursor-default"
		aria-label="Close modal" onclick={() => ui.closeHybridSearch()}></button>

	<div role="dialog" aria-modal="true" aria-labelledby="hybrid-search-title" tabindex="-1"
		class="relative z-10 w-full mx-4 bg-cream rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh] max-w-6xl">

		<div class="flex items-center justify-between px-6 py-4 bg-navy border-b border-navy-light">
			<h2 id="hybrid-search-title" class="text-lg font-sans font-semibold text-cream">Hybrid Search</h2>
			<button onclick={() => ui.closeHybridSearch()} class="text-sand hover:text-cream transition-colors" aria-label="Close">
				<svg class="w-5 h-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
				</svg>
			</button>
		</div>

		<!-- Search bar -->
		<div class="px-6 py-4 border-b border-sand bg-white">
			<div class="flex items-end gap-3">
				<div class="flex-1">
					<label for="hybrid-topic" class="block text-xs text-muted mb-1">
						Search the archival graph — semantic and keyword evidence combined; no AI assistant involved
					</label>
					<input id="hybrid-topic" type="text" bind:value={topic}
						placeholder="e.g. organizational charts"
						onkeydown={(e) => { if (e.key === 'Enter') search(); }}
						class="w-full px-3 py-2 border border-sand rounded-lg text-sm bg-white focus:outline-none focus:border-navy/50" />
				</div>
				<div>
					<label for="hybrid-limit" class="block text-xs text-muted mb-1">Max results</label>
					<select id="hybrid-limit" bind:value={limit}
						class="px-2 py-2 border border-sand rounded-lg text-sm bg-white">
						<option value={100}>100</option>
						<option value={200}>200</option>
						<option value={500}>500</option>
					</select>
				</div>
				<button onclick={search} disabled={loading || !topic.trim()}
					class="px-5 py-2 rounded-lg bg-navy text-cream text-sm font-medium disabled:opacity-40 hover:bg-navy-light transition-colors">
					{loading ? 'Searching…' : 'Search'}
				</button>
			</div>
			{#if errorMsg}<p class="mt-2 text-sm text-red-600">{errorMsg}</p>{/if}
		</div>

		<!-- Results -->
		<div class="flex-1 overflow-auto px-6 py-4">
			{#if loading}
				<p class="text-sm text-muted py-8 text-center">Running hybrid search…</p>
			{:else if searched === null}
				<p class="text-sm text-muted-light py-8 text-center">
					Results appear here, ranked by combined relevance. Titles link to ArchivesSpace.
				</p>
			{:else if results.length === 0}
				<p class="text-sm text-muted py-8 text-center">No records found for “{searched}”.</p>
			{:else}
				<div class="flex items-center gap-3 mb-3">
					<input type="text" bind:value={filter} oninput={() => (page = 1)}
						placeholder="Filter results — matches title, collection, and evidence"
						class="flex-1 max-w-md px-3 py-1.5 border border-sand rounded-lg text-xs bg-white focus:outline-none focus:border-navy/50" />
					{#if filter.trim()}
						<span class="text-xs text-muted">{filtered.length} of {results.length} shown</span>
					{/if}
					<button onclick={exportCsv} disabled={sorted.length === 0}
						class="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-sand text-xs text-charcoal hover:bg-sand/40 disabled:opacity-30 transition-colors"
						title="Export the current results (with filter and sort applied) as CSV">
						<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24">
							<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
						</svg>
						Export CSV
					</button>
				</div>
				{#if filtered.length === 0}
					<p class="text-sm text-muted py-8 text-center">No results match the filter “{filter}”.</p>
				{/if}
				<table class="w-full text-xs border-collapse">
					<thead>
						<tr class="text-left text-muted border-b border-sand select-none">
							{#each COLS as c (c.key)}
								<th class="py-2 pr-3 cursor-pointer hover:text-charcoal whitespace-nowrap"
									onclick={() => setSort(c.key)}>{c.label}{arrow(c.key)}</th>
							{/each}
							<th class="py-2">Evidence</th>
						</tr>
					</thead>
					<tbody>
						{#each pageRows as r (r.rank)}
							<tr class="border-b border-sand/50 align-top hover:bg-sand/20">
								<td class="py-1.5 pr-3 text-muted tabular-nums">{r.rank}</td>
								<td class="py-1.5 pr-3 max-w-xs">
									{#if r.aspace_url}
										<a href={r.aspace_url} target="_blank" rel="noopener"
											class="text-navy underline decoration-sand hover:decoration-navy">{r.title}</a>
									{:else}{r.title}{/if}
								</td>
								<td class="py-1.5 pr-3 whitespace-nowrap">{r.record_type}</td>
								<td class="py-1.5 pr-3 text-muted tabular-nums">{r.score}</td>
								<td class="py-1.5 pr-3">{r.match_source}</td>
								<td class="py-1.5 pr-3 max-w-[12rem]">
									{#if r.in_collection_url}
										<a href={r.in_collection_url} target="_blank" rel="noopener"
											class="text-navy underline decoration-sand hover:decoration-navy">{r.in_collection}</a>
									{:else}{r.in_collection ?? ''}{/if}
								</td>
								<td class="py-1.5 text-muted">{r.excerpt}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
		</div>

		<!-- Pager -->
		{#if results.length > 0}
			<div class="flex items-center justify-between px-6 py-3 border-t border-sand bg-white text-xs text-muted">
				<div class="flex items-center gap-2">
					<span>{filtered.length}{filter.trim() ? ` of ${results.length}` : ''} result{filtered.length === 1 ? '' : 's'} for “{searched}”</span>
					<span>·</span>
					<label for="hybrid-pagesize">Per page</label>
					<select id="hybrid-pagesize" bind:value={pageSize}
						onchange={() => (page = 1)}
						class="px-1.5 py-1 border border-sand rounded bg-white">
						<option value={10}>10</option>
						<option value={25}>25</option>
						<option value={50}>50</option>
						<option value={100}>100</option>
					</select>
				</div>
				<div class="flex items-center gap-3">
					<button onclick={() => (page = Math.max(1, page - 1))} disabled={page <= 1}
						class="px-2 py-1 rounded border border-sand disabled:opacity-30 hover:bg-sand/40">‹ Prev</button>
					<span class="tabular-nums">Page {page} of {pageCount}</span>
					<button onclick={() => (page = Math.min(pageCount, page + 1))} disabled={page >= pageCount}
						class="px-2 py-1 rounded border border-sand disabled:opacity-30 hover:bg-sand/40">Next ›</button>
				</div>
			</div>
		{/if}
	</div>
</div>
