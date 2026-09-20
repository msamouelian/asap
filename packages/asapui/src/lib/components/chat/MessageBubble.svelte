<script lang="ts">
	import { marked, Renderer } from 'marked';
	import { ui } from '$lib/stores/ui.svelte';
	import type { DisplayMessage, RagMsg, ThinkingMsg } from '$lib/stores/chat.svelte';
	import ToolCallBlock from './ToolCallBlock.svelte';
	import type { Result as VegaResult } from 'vega-embed';
	import {
		parseVegaSpec, vegaBlockHtml, embedChart, chartRows, rowsToCsv,
		downloadBlob, downloadImage, chartFileStem, escapeHtml,
	} from '$lib/vega';

	const { msg }: { msg: DisplayMessage } = $props();

	// ── Custom renderer: adds copy button to every fenced code block ─────────
	const renderer = new Renderer();
	renderer.code = ({ text, lang }: { text: string; lang?: string }) => {
		const langClass = lang ? ` class="language-${lang}"` : '';
		const escaped   = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
		const codeHtml = `<div class="code-block-wrapper"><button class="copy-code-btn" title="Copy code" aria-label="Copy code"><svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect width="13" height="13" x="9" y="9" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button><pre><code${langClass}>${escaped}</code></pre></div>`;
		// A fenced Vega-Lite spec becomes a chart container; the code block is
		// kept inside it (collapsed) as the chart's viewable/copyable source.
		// While the fence is still streaming the JSON is incomplete, parses as
		// null, and renders as a plain code block until it completes.
		return parseVegaSpec(text, lang) ? vegaBlockHtml(codeHtml) : codeHtml;
	};

	// ── Custom renderer: wrap tables with a CSV export button ────────────────
	const baseTable = Renderer.prototype.table;
	renderer.table = function (token: Parameters<Renderer['table']>[0]) {
		const tableHtml = baseTable.call(this, token);
		return `<div class="table-export-wrapper"><span class="table-export-btns"><button class="copy-table-btn" title="Copy for spreadsheet" aria-label="Copy table for spreadsheet"><svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect width="13" height="13" x="9" y="9" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button><button class="export-csv-btn" title="Export table as CSV" aria-label="Export table as CSV"><svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button></span>${tableHtml}</div>`;
	};

	marked.setOptions({ gfm: true, breaks: true });

	// ── CSV export ────────────────────────────────────────────────────────────

	// Plain cell value shared by both export formats: text content, with the
	// record link appended so it stays usable in the spreadsheet, and a
	// formula-injection guard for spreadsheet programs.
	function cellValue(cell: HTMLTableCellElement): string {
		let text = (cell.textContent ?? '').trim().replace(/\s+/g, ' ');
		const link = cell.querySelector('a[href]') as HTMLAnchorElement | null;
		if (link?.href && link.href !== text) text = text ? `${text} (${link.href})` : link.href;
		if (/^[=+\-@]/.test(text)) text = `'${text}`;
		return text;
	}

	function csvCell(cell: HTMLTableCellElement): string {
		let text = cellValue(cell);
		// Standard CSV quoting.
		if (/[",\n]/.test(text)) text = `"${text.replace(/"/g, '""')}"`;
		return text;
	}

	// TSV for the clipboard: spreadsheet programs paste tab-separated text
	// straight into cells. Tabs/newlines inside a cell become spaces (TSV has
	// no quoting convention that paste handlers agree on).
	function tableToTsv(table: HTMLTableElement): string {
		return Array.from(table.rows)
			.map(row => Array.from(row.cells)
				.map(c => cellValue(c).replace(/[\t\n]/g, ' '))
				.join('\t'))
			.join('\n');
	}

	function exportTableCsv(table: HTMLTableElement) {
		const rows = Array.from(table.rows).map(row =>
			Array.from(row.cells).map(csvCell).join(','),
		);
		// BOM so Excel opens UTF-8 (accented names in archival data) correctly.
		const blob = new Blob(['\uFEFF' + rows.join('\r\n')], { type: 'text/csv;charset=utf-8' });
		const url  = URL.createObjectURL(blob);
		const a    = document.createElement('a');
		a.href     = url;
		a.download = `asap-table-${new Date().toISOString().slice(0, 10)}.csv`;
		a.click();
		URL.revokeObjectURL(url);
	}

	const html = $derived(() => {
		if (msg.kind !== 'assistant') return '';
		const raw = marked.parse(msg.content, { renderer }) as string;
		// Style [Passage N] citations as chips so they read as references,
		// not stray brackets.
		return raw.replace(
			/\[Passage (\d+)\]/g,
			'<span class="passage-cite">Passage $1</span>',
		);
	});

	function legacyCopy(text: string, onSuccess: () => void) {
		const ta = document.createElement('textarea');
		ta.value = text;
		ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
		document.body.appendChild(ta);
		ta.focus();
		ta.select();
		try { document.execCommand('copy'); onSuccess(); } catch { /* ignore */ }
		ta.remove();
	}

	// Attach copy-button click handlers after each render
	let proseEl = $state<HTMLElement | null>(null);

	$effect(() => {
		if (!proseEl) return;
		void html(); // track html changes to re-attach after streaming updates
		// Charts are drawn only once the response has finished streaming: every
		// token replaces the {@html} DOM, and re-embedding a chart per token
		// would be wasteful. Reading msg.streaming here re-runs this effect
		// when the turn completes.
		const streaming = msg.kind === 'assistant' && !!msg.streaming;

		// Open all links (e.g. ArchivesSpace record links) in a new tab rather
		// than navigating away from the chat.
		proseEl.querySelectorAll<HTMLAnchorElement>('a[href]').forEach(a => {
			a.target = '_blank';
			a.rel = 'noopener noreferrer';
		});

		const buttons = proseEl.querySelectorAll<HTMLButtonElement>('.copy-code-btn');
		const cleanups: (() => void)[] = [];

		proseEl.querySelectorAll<HTMLButtonElement>('.export-csv-btn').forEach(btn => {
			const table = btn.closest('.table-export-wrapper')?.querySelector('table');
			if (!table) return;
			const handler = () => exportTableCsv(table);
			btn.addEventListener('click', handler);
			cleanups.push(() => btn.removeEventListener('click', handler));
		});

		proseEl.querySelectorAll<HTMLButtonElement>('.copy-table-btn').forEach(btn => {
			const table = btn.closest('.table-export-wrapper')?.querySelector('table');
			if (!table) return;
			const handler = () => {
				const tsv = tableToTsv(table);
				const finish = () => {
					btn.classList.add('copied');
					const orig = btn.title;
					btn.title = 'Copied!';
					setTimeout(() => { btn.classList.remove('copied'); btn.title = orig; }, 2000);
				};
				if (navigator.clipboard) {
					navigator.clipboard.writeText(tsv).then(finish).catch(() => legacyCopy(tsv, finish));
				} else {
					legacyCopy(tsv, finish);
				}
			};
			btn.addEventListener('click', handler);
			cleanups.push(() => btn.removeEventListener('click', handler));
		});

		buttons.forEach(btn => {
			const codeEl = btn.closest('.code-block-wrapper')?.querySelector('code');
			const handler = () => {
				const text = codeEl?.textContent ?? '';
				const finish = () => {
					btn.classList.add('copied');
					const orig = btn.title;
					btn.title = 'Copied!';
					setTimeout(() => { btn.classList.remove('copied'); btn.title = orig; }, 2000);
				};
				// navigator.clipboard requires a secure context (HTTPS/localhost).
				// Fall back to the legacy execCommand approach on plain HTTP.
				if (navigator.clipboard) {
					navigator.clipboard.writeText(text).then(finish).catch(() => legacyCopy(text, finish));
				} else {
					legacyCopy(text, finish);
				}
			};
			btn.addEventListener('click', handler);
			cleanups.push(() => btn.removeEventListener('click', handler));
		});

		// ── Vega-Lite charts ────────────────────────────────────────────────
		if (streaming) {
			proseEl.querySelectorAll<HTMLElement>('.vega-loading').forEach(el => {
				el.textContent = 'Chart renders when the response completes…';
			});
		} else {
			proseEl.querySelectorAll<HTMLElement>('.vega-block').forEach(block => {
				const chartEl = block.querySelector<HTMLElement>('.vega-chart');
				const specEl  = block.querySelector<HTMLElement>('.vega-spec');
				const codeEl  = specEl?.querySelector<HTMLElement>('code');
				if (!chartEl || !specEl || !codeEl) return;
				const spec = parseVegaSpec(codeEl.textContent ?? '', 'vega-lite');
				if (!spec) return;

				let result: VegaResult | null = null;
				let disposed = false;
				embedChart(chartEl, spec)
					.then(r => { if (disposed) r.finalize(); else result = r; })
					.catch((err: unknown) => {
						const detail = err instanceof Error ? err.message : String(err);
						chartEl.innerHTML = `<div class="vega-error">Chart could not be rendered: ${escapeHtml(detail)}</div>`;
						specEl.hidden = false;
					});
				cleanups.push(() => { disposed = true; result?.finalize(); });

				const on = (sel: string, fn: (btn: HTMLButtonElement) => void) => {
					const btn = block.querySelector<HTMLButtonElement>(sel);
					if (!btn) return;
					const handler = () => fn(btn);
					btn.addEventListener('click', handler);
					cleanups.push(() => btn.removeEventListener('click', handler));
				};
				const stem = () => chartFileStem(spec);
				on('.vega-csv-btn', () => {
					if (!result) return;
					const csv = rowsToCsv(chartRows(spec, result));
					downloadBlob(new Blob([csv], { type: 'text/csv;charset=utf-8' }), `${stem()}.csv`);
				});
				on('.vega-svg-btn', () => { if (result) void downloadImage(result, 'svg', `${stem()}.svg`); });
				on('.vega-png-btn', () => { if (result) void downloadImage(result, 'png', `${stem()}.png`); });
				on('.vega-spec-btn', btn => {
					specEl.hidden = !specEl.hidden;
					btn.setAttribute('aria-expanded', String(!specEl.hidden));
				});
			});
		}

		return () => cleanups.forEach(c => c());
	});

	// ── User message 3-dot menu ───────────────────────────────────────────────
	let userMenuOpen = $state(false);
	let userMenuPos  = $state<{ top: number; right: number } | null>(null);

	function openUserMenu(e: MouseEvent) {
		e.stopPropagation();
		if (userMenuOpen) { userMenuOpen = false; userMenuPos = null; return; }
		const btn  = e.currentTarget as HTMLElement;
		const rect = btn.getBoundingClientRect();
		userMenuPos  = { top: rect.bottom + 4, right: window.innerWidth - rect.right };
		userMenuOpen = true;
	}

	function closeUserMenu() {
		userMenuOpen = false;
		userMenuPos  = null;
	}
</script>

{#if msg.kind === 'user'}
	<!-- Fixed backdrop + dropdown for user message menu -->
	{#if userMenuOpen && userMenuPos}
		<div class="fixed inset-0 z-40" onpointerdown={() => closeUserMenu()}></div>
		<div
			class="fixed z-50 bg-white border border-sand rounded-lg shadow-lg py-1 w-52"
			style="top: {userMenuPos.top}px; right: {userMenuPos.right}px"
			onpointerdown={(e) => e.stopPropagation()}
		>
			<button
				onclick={() => {
					ui.openPromptForm({ mode: 'save', initialText: msg.content });
					closeUserMenu();
				}}
				class="w-full flex items-center gap-2 px-3 py-2 text-sm text-charcoal hover:bg-parchment text-left"
			>
				<svg class="w-3.5 h-3.5 shrink-0 text-muted" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" d="M5 5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16l-7-3.5L5 21V5z" />
				</svg>
				Save as prompt
			</button>
		</div>
	{/if}

	<div class="flex justify-end items-start gap-1 mb-4 group/user">
		<!-- 3-dot options button (left of bubble) -->
		<button
			onclick={openUserMenu}
			class="self-center p-1.5 rounded-md opacity-50 group-hover/user:opacity-100 transition-opacity text-muted hover:text-navy hover:bg-sand"
			aria-label="Message options"
		>
			<svg class="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
				<circle cx="12" cy="5"  r="2.2"/>
				<circle cx="12" cy="12" r="2.2"/>
				<circle cx="12" cy="19" r="2.2"/>
			</svg>
		</button>

		<div class="max-w-[75%] bg-navy text-cream rounded-2xl rounded-tr-sm px-4 py-3 text-sm leading-relaxed shadow-sm">
			{msg.content}
		</div>
	</div>

{:else if msg.kind === 'assistant'}
	<div class="flex items-start gap-3 mb-4">
		<div class="w-8 h-8 shrink-0 rounded-full bg-parchment border border-sand flex items-center justify-center text-base mt-0.5">
			📦
		</div>
		<div class="flex-1 min-w-0">
			<div bind:this={proseEl} class="prose max-w-none text-charcoal">
				{@html html()}
			</div>
			{#if msg.streaming}
				<span class="inline-block w-2 h-4 bg-navy/60 ml-0.5 animate-pulse rounded-sm align-middle"></span>
			{/if}
		</div>
	</div>

{:else if msg.kind === 'thinking'}
	<!-- Collapsible reasoning block — collapsed by default, expands on click -->
	<div class="flex items-start gap-3 mb-1">
		<div class="w-8 shrink-0"></div><!-- spacer aligns with assistant avatar -->
		<div class="flex-1 min-w-0">
			<div class="border border-sand/70 rounded-xl overflow-hidden text-xs">
				<button
					onclick={() => (msg as ThinkingMsg).expanded = !(msg as ThinkingMsg).expanded}
					class="w-full flex items-center gap-2 px-3 py-2 text-left text-muted hover:text-charcoal hover:bg-sand/20 transition-colors select-none"
				>
					<svg
						class={['w-3 h-3 shrink-0 transition-transform duration-150', msg.expanded ? 'rotate-90' : ''].join(' ')}
						fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"
					>
						<path stroke-linecap="round" stroke-linejoin="round" d="M9 18l6-6-6-6"/>
					</svg>
					{#if msg.streaming}
						<span class="italic">Thinking</span>
						<span class="inline-flex items-center gap-0.5 ml-0.5">
							<span class="w-1 h-1 bg-muted rounded-full animate-bounce [animation-delay:0ms]"></span>
							<span class="w-1 h-1 bg-muted rounded-full animate-bounce [animation-delay:150ms]"></span>
							<span class="w-1 h-1 bg-muted rounded-full animate-bounce [animation-delay:300ms]"></span>
						</span>
					{:else}
						<span>Reasoning</span>
					{/if}
				</button>
				{#if msg.expanded}
					<div class="px-3 py-2.5 font-mono leading-relaxed whitespace-pre-wrap bg-sand/20 border-t border-sand/40 max-h-72 overflow-y-auto text-muted">
						{msg.content}
					</div>
				{/if}
			</div>
		</div>
	</div>

{:else if msg.kind === 'rag'}
	<!-- Retrieved document context — collapsed by default (evidence on demand) -->
	<div class="flex items-start gap-3 mb-1">
		<div class="w-8 shrink-0"></div><!-- spacer aligns with assistant avatar -->
		<div class="flex-1 min-w-0">
			<div class="border border-sand/70 rounded-xl overflow-hidden text-xs">
				<button
					onclick={() => (msg as RagMsg).expanded = !(msg as RagMsg).expanded}
					class="w-full flex items-center gap-2 px-3 py-2 text-left text-muted hover:text-charcoal hover:bg-sand/20 transition-colors select-none"
				>
					<svg
						class={['w-3 h-3 shrink-0 transition-transform duration-150', msg.expanded ? 'rotate-90' : ''].join(' ')}
						fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"
					>
						<path stroke-linecap="round" stroke-linejoin="round" d="M9 18l6-6-6-6"/>
					</svg>
					<span>📚 Retrieved context ({msg.chunks.length} passage{msg.chunks.length === 1 ? '' : 's'})</span>
				</button>
				{#if msg.expanded}
					<div class="px-3 py-2.5 bg-sand/20 border-t border-sand/40 max-h-80 overflow-y-auto flex flex-col gap-3">
						{#if (msg as RagMsg).searchQuery}
							<p class="text-muted-light italic">
								🔍 Search query: “{(msg as RagMsg).searchQuery}”
							</p>
						{/if}
						{#each msg.chunks as c, n (c.id)}
							<div>
								<p class="font-medium text-charcoal">
									[Passage {n + 1}] {c.file_name}{c.page_no != null ? ` · p. ${c.page_no}` : ''}{c.heading ? ` · § ${c.heading}` : ''}
									<span class="text-muted-light font-normal"> — {c.collection}</span>
								</p>
								<p class="text-muted leading-relaxed whitespace-pre-wrap mt-0.5">{c.text}</p>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		</div>
	</div>

{:else if msg.kind === 'tool_call'}
	<div class="mb-2 pl-11">
		<ToolCallBlock {msg} />
	</div>

{:else if msg.kind === 'error'}
	<div class="flex items-start gap-3 mb-4">
		<div class="w-8 h-8 shrink-0 rounded-full bg-red-50 border border-red-200 flex items-center justify-center text-sm mt-0.5">
			⚠️
		</div>
		<div class="flex-1 bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-sm text-red-700">
			{msg.detail}
		</div>
	</div>
{/if}
