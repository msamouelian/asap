/**
 * Vega-Lite chart support for assistant messages.
 *
 * The LLM emits a chart as a fenced code block (```vega-lite … ```) holding a
 * Vega-Lite JSON spec. MessageBubble's markdown renderer swaps such blocks for
 * a chart container (see `vegaBlockHtml`), and after each render `embedChart`
 * draws the spec into it with vega-embed. vega/vega-lite/vega-embed are large
 * (~1 MB), so they are imported lazily the first time a chart is rendered.
 */

import type { Result, EmbedOptions } from 'vega-embed';

export type VegaSpec = Record<string, unknown>;

// ── Detection ───────────────────────────────────────────────────────────────

const VEGA_LANGS = new Set(['vega-lite', 'vegalite', 'vega_lite', 'vl', 'vega']);

/**
 * Return the parsed spec if this fenced code block holds a Vega-Lite (or
 * Vega) specification, else null. A `vega-lite` fence tag is authoritative;
 * `json` / untagged fences are sniffed for a Vega `$schema` or the
 * mark/encoding shape so charts still render when the model forgets the tag.
 * Incomplete JSON (a fence still streaming in) parses as null and therefore
 * falls back to the ordinary code block until the fence is complete.
 */
export function parseVegaSpec(text: string, lang?: string): VegaSpec | null {
	const tag = (lang ?? '').trim().toLowerCase();
	if (tag && !VEGA_LANGS.has(tag) && tag !== 'json') return null;
	let spec: unknown;
	try {
		spec = JSON.parse(text);
	} catch {
		return null;
	}
	if (!spec || typeof spec !== 'object' || Array.isArray(spec)) return null;
	const s = spec as VegaSpec;
	if (VEGA_LANGS.has(tag)) return s;
	const schema = typeof s.$schema === 'string' ? s.$schema : '';
	if (/vega\.github\.io\/schema\/vega(-lite)?\//.test(schema)) return s;
	const hasView = ['mark', 'layer', 'vconcat', 'hconcat', 'concat', 'facet', 'repeat'].some(k => k in s);
	return hasView && ('encoding' in s || 'data' in s || 'spec' in s) ? s : null;
}

function isVegaMode(spec: VegaSpec): 'vega' | 'vega-lite' {
	const schema = typeof spec.$schema === 'string' ? spec.$schema : '';
	return /\/schema\/vega\//.test(schema) ? 'vega' : 'vega-lite';
}

// ── Markup emitted by the markdown renderer ─────────────────────────────────

const ICON_DOWNLOAD =
	'<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
const ICON_CODE =
	'<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';

/**
 * HTML for a chart block. `codeBlockHtml` is the ordinary rendered code block
 * (with its copy button); it is kept, collapsed, as the chart's source so the
 * user can copy or inspect the spec. The spec text is re-read from that code
 * element at embed time, which avoids escaping JSON into an attribute.
 */
export function vegaBlockHtml(codeBlockHtml: string): string {
	const label = 'Vega-Lite chart';
	return (
		`<div class="vega-block">` +
		`<div class="vega-toolbar">` +
		`<span class="vega-title">${label}</span>` +
		`<span class="vega-btns">` +
		`<button class="vega-btn vega-csv-btn" title="Download chart data as CSV" aria-label="Download chart data as CSV">${ICON_DOWNLOAD}<span>CSV</span></button>` +
		`<button class="vega-btn vega-svg-btn" title="Download chart as SVG" aria-label="Download chart as SVG">${ICON_DOWNLOAD}<span>SVG</span></button>` +
		`<button class="vega-btn vega-png-btn" title="Download chart as PNG" aria-label="Download chart as PNG">${ICON_DOWNLOAD}<span>PNG</span></button>` +
		`<button class="vega-btn vega-spec-btn" title="Show or hide the Vega-Lite spec" aria-label="Show or hide the Vega-Lite spec" aria-expanded="false">${ICON_CODE}<span>Spec</span></button>` +
		`</span></div>` +
		`<div class="vega-chart" aria-busy="true"><span class="vega-loading">Rendering chart…</span></div>` +
		`<div class="vega-spec" hidden>${codeBlockHtml}</div>` +
		`</div>`
	);
}

export function escapeHtml(s: string): string {
	return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** Title text from a spec's `title` (string or {text}) for the toolbar. */
export function specTitle(spec: VegaSpec): string | undefined {
	const t = spec.title;
	if (typeof t === 'string') return t;
	if (t && typeof t === 'object' && typeof (t as { text?: unknown }).text === 'string') {
		return (t as { text: string }).text;
	}
	return undefined;
}

// ── Embedding ───────────────────────────────────────────────────────────────

/**
 * Draw `spec` into `el`. Single-view and layered specs without an explicit
 * width are stretched to the container so charts fill the message bubble;
 * concat/facet specs keep Vega-Lite's own sizing ("container" is unsupported
 * there). Returns the vega-embed result; call `result.finalize()` on cleanup.
 */
export async function embedChart(el: HTMLElement, spec: VegaSpec): Promise<Result> {
	const { default: vegaEmbed } = await import('vega-embed');
	const mode = isVegaMode(spec);
	const prepared: VegaSpec = { ...spec };
	if (mode === 'vega-lite') {
		const single = 'mark' in spec || 'layer' in spec;
		const isArc = spec.mark === 'arc' || (typeof spec.mark === 'object' && spec.mark !== null && (spec.mark as { type?: string }).type === 'arc');
		if (single && !isArc && !('width' in spec)) prepared.width = 'container';
		if (single && !('height' in spec)) prepared.height = 280;
		// Text-heavy archival labels: let long axis labels breathe.
		prepared.autosize = prepared.autosize ?? { type: 'fit-x', contains: 'padding' };
	}
	const opts: EmbedOptions = {
		mode,
		renderer: 'svg',
		actions: false, // our own toolbar provides export; no external "open in editor" link
		config: {
			font: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
			background: 'transparent',
		},
	};
	el.innerHTML = '';
	el.removeAttribute('aria-busy');
	return vegaEmbed(el, prepared as never, opts);
}

// ── Export helpers ──────────────────────────────────────────────────────────

/**
 * Rows behind the chart. Prefers the spec's inline `data.values`; otherwise
 * the first compiled dataset (`source_0`, what Vega-Lite names the primary
 * source) from the live view.
 */
export function chartRows(spec: VegaSpec, result: Result): Record<string, unknown>[] {
	const data = spec.data as { values?: unknown } | undefined;
	if (data && Array.isArray(data.values)) return data.values as Record<string, unknown>[];
	try {
		const rows = result.view.data('source_0') as Record<string, unknown>[];
		if (Array.isArray(rows)) return rows;
	} catch {
		/* dataset name differs; fall through */
	}
	return [];
}

export function rowsToCsv(rows: Record<string, unknown>[]): string {
	const cols: string[] = [];
	for (const r of rows) for (const k of Object.keys(r)) if (!cols.includes(k) && !k.startsWith('_')) cols.push(k);
	const cell = (v: unknown): string => {
		let s: string;
		if (v == null) s = '';
		else if (v instanceof Date) s = v.toISOString();
		else if (typeof v === 'object') s = JSON.stringify(v);
		else s = String(v);
		if (/^[=+\-@]/.test(s)) s = `'${s}`; // spreadsheet formula-injection guard
		return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
	};
	const lines = [cols.map(cell).join(',')];
	for (const r of rows) lines.push(cols.map(c => cell(r[c])).join(','));
	return '﻿' + lines.join('\r\n');
}

export function downloadBlob(blob: Blob, filename: string) {
	const url = URL.createObjectURL(blob);
	const a = document.createElement('a');
	a.href = url;
	a.download = filename;
	a.click();
	setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function downloadImage(result: Result, kind: 'svg' | 'png', filename: string) {
	const url = await result.view.toImageURL(kind, kind === 'png' ? 2 : 1);
	const a = document.createElement('a');
	a.href = url;
	a.download = filename;
	a.click();
}

export function chartFileStem(spec: VegaSpec): string {
	const t = specTitle(spec) ?? 'chart';
	const slug = t.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'chart';
	return `asap-${slug}-${new Date().toISOString().slice(0, 10)}`;
}
