/**
 * Markdown → HTML for assistant messages.
 *
 * Model output is untrusted: it can be steered by prompt injection carried in
 * archival notes, uploaded documents, or shared prompts. Everything the model
 * emits therefore passes through DOMPurify before it reaches `{@html}`.
 *
 * Pipeline: marked (GFM, custom code/table renderers that add the copy and
 * export toolbars) → DOMPurify with a restricted URL policy → HTML string.
 *
 * The only things allowed to survive that the model did not write are the
 * toolbar buttons/icons emitted by our own renderers; the sanitizer config
 * below is deliberately wide enough for those (button, svg icon paths,
 * aria-*, hidden) and no wider.
 */

import { Marked, Renderer, type Tokens } from 'marked';
import DOMPurify from 'dompurify';
import { parseVegaSpec, vegaBlockHtml, escapeHtml } from '$lib/vega';

// ── marked ──────────────────────────────────────────────────────────────────

const ICON_COPY =
	'<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect width="13" height="13" x="9" y="9" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
const ICON_COPY_14 = ICON_COPY.replace(/width="13" height="13"/, 'width="14" height="14"');
const ICON_DOWNLOAD =
	'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';

const renderer = new Renderer();

// Fenced code: add a copy button; a Vega-Lite spec becomes a chart container
// that keeps the code block inside it (collapsed) as the viewable source.
//
// `lang` is the whole fence info string, not just the first word, and marked's
// default renderer both truncates and escapes it. Doing neither would let a
// fence such as ```js" onpointerenter="…" write attributes into <code>.
renderer.code = ({ text, lang }: Tokens.Code) => {
	const langWord  = (lang ?? '').match(/^\S*/)?.[0] ?? '';
	const langClass = langWord ? ` class="language-${escapeHtml(langWord)}"` : '';
	const codeHtml =
		`<div class="code-block-wrapper">` +
		`<button class="copy-code-btn" title="Copy code" aria-label="Copy code">${ICON_COPY}</button>` +
		`<pre><code${langClass}>${escapeHtml(text)}</code></pre>` +
		`</div>`;
	// While the fence is still streaming the JSON is incomplete, parses as
	// null, and renders as a plain code block until it completes.
	return parseVegaSpec(text, langWord) ? vegaBlockHtml(codeHtml) : codeHtml;
};

// Tables: wrap with copy-as-TSV and export-as-CSV buttons.
const baseTable = Renderer.prototype.table;
renderer.table = function (token: Tokens.Table) {
	const tableHtml = baseTable.call(this, token);
	return (
		`<div class="table-export-wrapper"><span class="table-export-btns">` +
		`<button class="copy-table-btn" title="Copy for spreadsheet" aria-label="Copy table for spreadsheet">${ICON_COPY_14}</button>` +
		`<button class="export-csv-btn" title="Export table as CSV" aria-label="Export table as CSV">${ICON_DOWNLOAD}</button>` +
		`</span>${tableHtml}</div>`
	);
};

// A dedicated instance so options/renderer never leak into other callers of
// the global `marked` singleton.
const md = new Marked({ gfm: true, breaks: true, renderer });

// ── DOMPurify ───────────────────────────────────────────────────────────────

// Links may only point at http(s) or mailto. Anything else (javascript:,
// data:, vbscript:, blob:, relative paths that could hit our own API) is
// dropped. Enforced on anchors in the hook below rather than through
// DOMPurify's ALLOWED_URI_REGEXP, because that regex is also applied to
// ordinary attribute values (svg path data, width, viewBox …) and a strict
// scheme-only pattern would strip them all. DOMPurify's default URI regex
// still rejects javascript:/data:/vbscript: on every other attribute.
const ALLOWED_LINK = /^(?:https?:|mailto:)/i;
const SVG_NS = 'http://www.w3.org/2000/svg';

// Tags the model must never get rendered even though DOMPurify would allow
// them by default. img is out because a remote image is a zero-click request
// carrying whatever the model puts in the URL; forms because a rendered form
// inside a trusted chat bubble is a phishing surface; style/math because they
// enlarge the mXSS surface and nothing in chat output needs them.
const FORBID_TAGS = [
	'img', 'picture', 'source', 'video', 'audio', 'track',
	'form', 'input', 'textarea', 'select', 'option',
	'style', 'math', 'iframe', 'object', 'embed', 'base', 'meta', 'link',
];

// Attributes our own toolbars need beyond DOMPurify's default allowlist.
const ADD_ATTR = ['target', 'aria-expanded', 'aria-busy'];

const purify = DOMPurify();

purify.setConfig({
	USE_PROFILES: { html: true, svg: true },
	FORBID_TAGS,
	// Inline styles are allowed by default; a model could use them to paint a
	// fake login form over the app or fire a CSS url() beacon. Our toolbars
	// use classes only.
	FORBID_ATTR: ['style'],
	ADD_ATTR,
	// Never let a sanitized node keep a DOM-clobbering id/name.
	SANITIZE_DOM: true,
});

// Every surviving link opens in a new tab and drops the referrer, so a link
// in a message can neither navigate the chat away nor learn where it came
// from. Done here (not in a post-render effect) so the guarantee is part of
// the string the component receives and is unit-testable.
purify.addHook('afterSanitizeAttributes', node => {
	// nodeName rather than tagName: an SVG <a> reports a lowercase tagName.
	if (node.nodeName.toLowerCase() === 'a') {
		const href = node.getAttribute('href')?.trim() ?? '';
		// Only HTML anchors with an allowed scheme stay links. SVG anchors are
		// not something our icons use, so they are always demoted to text.
		if (href && node.namespaceURI !== SVG_NS && ALLOWED_LINK.test(href)) {
			node.setAttribute('target', '_blank');
			node.setAttribute('rel', 'noopener noreferrer');
		} else {
			node.removeAttribute('href');
			node.removeAttribute('target');
			node.removeAttribute('rel');
		}
	}
	if (node.hasAttribute('xlink:href')) node.removeAttribute('xlink:href');
});

// ── Public API ──────────────────────────────────────────────────────────────

/**
 * Render an assistant message body to sanitized HTML.
 *
 * Safe to call on partial (still streaming) markdown; marked tolerates
 * unterminated constructs and the sanitizer sees the same well-formed DOM a
 * browser would build.
 */
export function renderMarkdown(content: string): string {
	const raw = md.parse(content, { async: false }) as string;
	// Style [Passage N] citations as chips so they read as references, not
	// stray brackets. Applied before sanitizing so the span goes through the
	// same policy as everything else.
	const cited = raw.replace(/\[Passage (\d+)\]/g, '<span class="passage-cite">Passage $1</span>');
	if (!purify.isSupported) {
		// No DOM available (should not happen in the browser). Fail closed:
		// show the text, never unsanitized markup.
		return `<pre>${escapeHtml(content)}</pre>`;
	}
	return purify.sanitize(cited);
}
