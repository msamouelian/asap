import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { startPoller } from './polling';

type Status = { is_running: boolean };

function fakeDoc(state: DocumentVisibilityState = 'visible') {
	const listeners: Record<string, Set<() => void>> = {};
	const doc = {
		visibilityState: state,
		addEventListener: (t: string, cb: () => void) => { (listeners[t] ??= new Set()).add(cb); },
		removeEventListener: (t: string, cb: () => void) => { listeners[t]?.delete(cb); },
		fire: (t: string) => { listeners[t]?.forEach(cb => cb()); },
		count: (t: string) => listeners[t]?.size ?? 0,
		setState(s: DocumentVisibilityState) { doc.visibilityState = s; },
	};
	return doc as typeof doc & Pick<Document, 'addEventListener' | 'removeEventListener' | 'visibilityState'>;
}
function fakeWin() {
	const listeners: Record<string, Set<() => void>> = {};
	const win = {
		addEventListener: (t: string, cb: () => void) => { (listeners[t] ??= new Set()).add(cb); },
		removeEventListener: (t: string, cb: () => void) => { listeners[t]?.delete(cb); },
		fire: (t: string) => { listeners[t]?.forEach(cb => cb()); },
		count: (t: string) => listeners[t]?.size ?? 0,
	};
	return win as typeof win & Pick<Window, 'addEventListener' | 'removeEventListener'>;
}

// Under fake timers a real setTimeout never fires; advance the fake clock instead.
const flush = () => vi.advanceTimersByTimeAsync(0);

beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { vi.useRealTimers(); });

describe('startPoller', () => {
	it('ticks on the interval and reports each result with a timestamp', async () => {
		const fetch = vi.fn<() => Promise<Status>>().mockResolvedValue({ is_running: true });
		const onUpdate = vi.fn();
		const p = startPoller({ fetch, intervalMs: 1000, isActive: s => s.is_running, onUpdate, doc: fakeDoc(), win: fakeWin() });
		await vi.advanceTimersByTimeAsync(3000);
		expect(fetch).toHaveBeenCalledTimes(3);
		expect(onUpdate).toHaveBeenCalledTimes(3);
		expect(onUpdate.mock.calls[0][1]).toBeInstanceOf(Date);
		p.stop();
	});

	it('refreshes immediately when the tab becomes visible, without waiting for the tick', async () => {
		const fetch = vi.fn<() => Promise<Status>>().mockResolvedValue({ is_running: true });
		const doc = fakeDoc('hidden'); const win = fakeWin();
		const p = startPoller({ fetch, intervalMs: 30_000, isActive: s => s.is_running, onUpdate: () => {}, doc, win });
		expect(fetch).not.toHaveBeenCalled();
		doc.setState('visible'); doc.fire('visibilitychange');
		await flush(); await vi.advanceTimersByTimeAsync(0);
		expect(fetch).toHaveBeenCalledTimes(1);
		p.stop();
	});

	it('ignores visibilitychange to hidden', async () => {
		const fetch = vi.fn<() => Promise<Status>>().mockResolvedValue({ is_running: true });
		const doc = fakeDoc('hidden');
		const p = startPoller({ fetch, intervalMs: 30_000, isActive: s => s.is_running, onUpdate: () => {}, doc, win: fakeWin() });
		doc.fire('visibilitychange');
		await flush();
		expect(fetch).not.toHaveBeenCalled();
		p.stop();
	});

	it('refreshes on window focus', async () => {
		const fetch = vi.fn<() => Promise<Status>>().mockResolvedValue({ is_running: true });
		const win = fakeWin();
		const p = startPoller({ fetch, intervalMs: 30_000, isActive: s => s.is_running, onUpdate: () => {}, doc: fakeDoc(), win });
		win.fire('focus');
		await flush();
		expect(fetch).toHaveBeenCalledTimes(1);
		p.stop();
	});

	it('stops itself and detaches listeners once the result is no longer active', async () => {
		const fetch = vi.fn<() => Promise<Status>>()
			.mockResolvedValueOnce({ is_running: true })
			.mockResolvedValueOnce({ is_running: false })
			.mockResolvedValue({ is_running: false });
		const doc = fakeDoc(); const win = fakeWin();
		const onUpdate = vi.fn();
		const p = startPoller({ fetch, intervalMs: 1000, isActive: s => s.is_running, onUpdate, doc, win });
		await vi.advanceTimersByTimeAsync(2000);
		expect(p.stopped).toBe(true);
		expect(onUpdate).toHaveBeenLastCalledWith({ is_running: false }, expect.any(Date));
		expect(doc.count('visibilitychange')).toBe(0);
		expect(win.count('focus')).toBe(0);
		await vi.advanceTimersByTimeAsync(5000);
		expect(fetch).toHaveBeenCalledTimes(2);
	});

	it('reports errors and keeps polling instead of swallowing them', async () => {
		const fetch = vi.fn<() => Promise<Status>>()
			.mockRejectedValueOnce(new Error('network'))
			.mockResolvedValue({ is_running: false });
		const onError = vi.fn(); const onUpdate = vi.fn();
		const p = startPoller({ fetch, intervalMs: 1000, isActive: s => s.is_running, onUpdate, onError, doc: fakeDoc(), win: fakeWin() });
		await vi.advanceTimersByTimeAsync(1000);
		expect(onError).toHaveBeenCalledTimes(1);
		expect(onError.mock.calls[0][0]).toBeInstanceOf(Error);
		expect(p.stopped).toBe(false);
		await vi.advanceTimersByTimeAsync(1000);
		expect(onUpdate).toHaveBeenCalledWith({ is_running: false }, expect.any(Date));
		expect(p.stopped).toBe(true);
	});

	it('does not overlap fetches when a wake-up arrives mid-fetch', async () => {
		let resolve!: (s: Status) => void;
		const fetch = vi.fn<() => Promise<Status>>().mockImplementation(() => new Promise(r => { resolve = r; }));
		const doc = fakeDoc(); const win = fakeWin();
		const p = startPoller({ fetch, intervalMs: 1000, isActive: s => s.is_running, onUpdate: () => {}, doc, win });
		await vi.advanceTimersByTimeAsync(1000); // tick → fetch #1 pending
		win.fire('focus'); doc.fire('visibilitychange'); // wake-ups while pending
		await flush();
		expect(fetch).toHaveBeenCalledTimes(1);
		resolve({ is_running: true });
		await flush();
		win.fire('focus'); await flush();
		expect(fetch).toHaveBeenCalledTimes(2);
		p.stop();
	});

	it('ignores results that land after stop()', async () => {
		let resolve!: (s: Status) => void;
		const fetch = vi.fn<() => Promise<Status>>().mockImplementation(() => new Promise(r => { resolve = r; }));
		const onUpdate = vi.fn();
		const p = startPoller({ fetch, intervalMs: 1000, isActive: s => s.is_running, onUpdate, doc: fakeDoc(), win: fakeWin() });
		await vi.advanceTimersByTimeAsync(1000);
		p.stop();
		resolve({ is_running: true });
		await flush();
		expect(onUpdate).not.toHaveBeenCalled();
	});
});
