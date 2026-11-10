/**
 * Status polling that stays honest in background tabs.
 *
 * Browsers throttle timers in hidden tabs and freeze them outright after a
 * few minutes (Chrome energy saver, Safari). A panel that relies on
 * setInterval alone can therefore sit on a stale "running" result long after
 * the job finished, until the next tick happens to fire. This poller:
 *
 *  - ticks on an interval while the watched thing is active;
 *  - refreshes immediately when the tab becomes visible or regains focus;
 *  - never overlaps fetches (a wake-up during an in-flight fetch is skipped);
 *  - reports every result and every error to the caller instead of
 *    swallowing failures, so the UI can say "last checked at …" and
 *    "could not refresh";
 *  - stops itself once the caller says the thing is no longer active.
 */

export interface PollerOptions<T> {
	/** Fetch the current state. */
	fetch: () => Promise<T>;
	/** Interval between ticks while active. */
	intervalMs: number;
	/** Whether polling should continue after this result. */
	isActive: (value: T) => boolean;
	/** Called with every successful result. */
	onUpdate: (value: T, checkedAt: Date) => void;
	/** Called with every failed fetch. Polling continues. */
	onError?: (error: unknown, failedAt: Date) => void;
	/** Injection points for tests. */
	now?: () => Date;
	doc?: Pick<Document, 'addEventListener' | 'removeEventListener' | 'visibilityState'>;
	win?: Pick<Window, 'addEventListener' | 'removeEventListener'>;
}

export interface Poller {
	/** Fetch now (used on wake-up). Resolves when the fetch settles. */
	refreshNow(): Promise<void>;
	/** Stop ticking and detach listeners. Idempotent. */
	stop(): void;
	readonly stopped: boolean;
}

export function startPoller<T>(opts: PollerOptions<T>): Poller {
	const now = opts.now ?? (() => new Date());
	const doc = opts.doc ?? (typeof document !== 'undefined' ? document : undefined);
	const win = opts.win ?? (typeof window !== 'undefined' ? window : undefined);

	let timer: ReturnType<typeof setInterval> | null = null;
	let inFlight: Promise<void> | null = null;
	let stopped = false;

	async function run(): Promise<void> {
		try {
			const value = await opts.fetch();
			if (stopped) return;
			opts.onUpdate(value, now());
			if (!opts.isActive(value)) stop();
		} catch (err) {
			if (!stopped) opts.onError?.(err, now());
		}
	}

	function refreshNow(): Promise<void> {
		if (stopped) return Promise.resolve();
		if (!inFlight) {
			inFlight = run().finally(() => { inFlight = null; });
		}
		return inFlight;
	}

	function onVisibility() {
		if (!doc || doc.visibilityState === 'visible') void refreshNow();
	}
	function onFocus() {
		void refreshNow();
	}

	function stop() {
		if (stopped) return;
		stopped = true;
		if (timer) { clearInterval(timer); timer = null; }
		doc?.removeEventListener('visibilitychange', onVisibility);
		win?.removeEventListener('focus', onFocus);
	}

	timer = setInterval(() => { void refreshNow(); }, opts.intervalMs);
	doc?.addEventListener('visibilitychange', onVisibility);
	win?.addEventListener('focus', onFocus);

	return {
		refreshNow,
		stop,
		get stopped() { return stopped; },
	};
}
